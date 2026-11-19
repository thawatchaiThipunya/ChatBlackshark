from __future__ import annotations

import os
import hmac
import secrets
import uuid
import base64
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import FastAPI, HTTPException, Cookie, Depends, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from .engine_v2 import ProductChatEngine, get_catalog
from .store import SupabaseStore
from .chat_inputs import normalize_chat_input
from .shopee_products import lookup_shopee_item
from .admin_auth import ADMIN_SESSION_COOKIE, require_admin as require_admin_session
from .vision import describe_batch_with_key_rotation, describe_media_batch, describe_openrouter_batch


app = FastAPI(
    title="Black Shark Product Chat API",
    version="1.0.0",
    description="Answers only from the two supplied Black Shark workbooks.",
)

logger = logging.getLogger("blackshark.api")


class RedactSsoAccessLog(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and "/api/auth/sso/callback" in str(args[2]):
            safe_args = list(args)
            safe_args[2] = "/api/auth/sso/callback?[redacted]"
            record.args = tuple(safe_args)
        return True


logging.getLogger("uvicorn.access").addFilter(RedactSsoAccessLog())


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    request_id = request.headers.get("x-request-id") or secrets.token_hex(8)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed %s %s request_id=%s", request.method, request.url.path, request_id)
        raise
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    if request.url.path != "/api/health":
        logger.info(json.dumps({
            "event": "http_request",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": elapsed_ms,
        }, separators=(",", ":")))
    response.headers["X-Request-ID"] = request_id
    return response

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Request-ID"],
)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)
    product_name: str | None = Field(default=None, max_length=200)


class ChatRequest(BaseModel):
    question: str = Field(default="", max_length=1000)
    event: dict | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    product_name: str | None = None
    model_name: str | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    history: list[ChatTurn] = Field(default_factory=list, max_length=24)


engine = ProductChatEngine()
store = engine.store


def guest_or_401(guest: str | None) -> str:
    if not guest or len(guest) < 32:
        raise HTTPException(status_code=401, detail="chat_guest_required")
    return guest


def valid_session(session_id: str) -> str:
    try:
        return str(uuid.UUID(session_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail="invalid_session_id")


def current_admin(request: Request) -> dict:
    return require_admin_session(request, store)


def app_base_url(request: Request) -> str:
    configured = os.getenv("APP_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    if os.getenv("VERCEL") == "1":
        raise HTTPException(status_code=503, detail="app_base_url_not_configured")
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost:3000"
    proto = request.headers.get("x-forwarded-proto") or ("http" if host.startswith(("localhost", "127.0.0.1")) else "https")
    return f"{proto.split(',')[0].strip()}://{host.split(',')[0].strip()}".rstrip("/")


def set_auth_cookie(response: Response, token: str, max_age: int, secure: bool) -> None:
    response.set_cookie(
        ADMIN_SESSION_COOKIE, token, httponly=True, secure=secure,
        samesite="lax", max_age=max_age, path="/",
    )


def login_redirect(request: Request, code: str, clear_cookie: bool = True) -> RedirectResponse:
    base = app_base_url(request)
    response = RedirectResponse(f"{base}/login?error={urllib.parse.quote(code)}", status_code=302)
    if clear_cookie:
        response.delete_cookie(ADMIN_SESSION_COOKIE, path="/")
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


def clean_return_to(value: str | None) -> str:
    if (not value or not value.startswith("/") or value.startswith("//") or "\\" in value
            or any(ord(char) < 32 for char in value)):
        return "/dashboard"
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return "/dashboard"
    return value[:500]


def encode_sso_flow(state: str, return_to: str) -> str:
    if not store.token:
        raise HTTPException(status_code=503, detail="sso_session_signing_unavailable")
    payload = json.dumps({"state": state, "return_to": return_to}, separators=(",", ":")).encode()
    signature = hmac.new(store.token.encode(), payload, "sha256").digest()
    encoded_payload = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    encoded_signature = base64.urlsafe_b64encode(signature).decode().rstrip("=")
    return f"flow.{encoded_payload}.{encoded_signature}"


def decode_sso_flow(value: str) -> dict:
    if not store.token or not value.startswith("flow."):
        raise ValueError("invalid SSO flow cookie")
    try:
        encoded_payload, encoded_signature = value[5:].split(".", 1)
        payload = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        expected = hmac.new(store.token.encode(), payload, "sha256").digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("invalid SSO flow signature")
        flow = json.loads(payload)
        if not isinstance(flow, dict):
            raise ValueError("invalid SSO flow payload")
        return flow
    except (ValueError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid SSO flow cookie") from exc


@app.get("/api/auth/sso/login")
def start_sso(request: Request, return_to: str | None = Query(default=None)) -> RedirectResponse:
    base = app_base_url(request)
    callback = f"{base}/api/auth/sso/callback"
    return_path = clean_return_to(return_to)
    flow_state = secrets.token_urlsafe(32)
    flow_cookie = encode_sso_flow(flow_state, return_path)
    provider = os.getenv("SELLCENTER_OAUTH_BASE_URL", "https://data.digital.in.th").rstrip("/")
    params = urllib.parse.urlencode({
        "redirect_uri": callback,
        "app_name": os.getenv("SSO_APP_NAME", "Black Shark Chat"),
        "state": flow_state,
    })
    secure = base.startswith("https://")
    response = RedirectResponse(f"{provider}/system81/login?{params}", status_code=302)
    set_auth_cookie(response, flow_cookie, 600, secure)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/api/auth/sso/callback")
def finish_sso(request: Request) -> RedirectResponse:
    base = app_base_url(request)
    flow_cookie = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    try:
        flow = decode_sso_flow(flow_cookie)
    except ValueError:
        return login_redirect(request, "sso_expired")

    query = request.query_params
    provider_error = query.get("error")
    token = query.get("token")
    callback_state = query.get("state")
    if provider_error:
        return login_redirect(request, "sso_failed")
    if callback_state and not secrets.compare_digest(callback_state, str(flow.get("state", ""))):
        return login_redirect(request, "sso_state_invalid")
    if not token:
        return login_redirect(request, "no_token")

    provider = os.getenv("SELLCENTER_OAUTH_BASE_URL", "https://data.digital.in.th").rstrip("/")
    try:
        profile_request = urllib.request.Request(
            f"{provider}/system81/userinfo",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        with urllib.request.urlopen(profile_request, timeout=10) as profile_response:
            profile_data = json.load(profile_response)
        if not isinstance(profile_data, dict):
            raise ValueError("invalid SSO profile")
        profile = profile_data.get("user", profile_data) if profile_data.get("success") else profile_data
        if not isinstance(profile, dict):
            raise ValueError("invalid SSO profile")
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
        logger.warning("sso_userinfo_failed")
        return login_redirect(request, "userinfo_failed")

    username = str(profile.get("username") or "").strip()
    email = str(profile.get("email") or username).strip().lower()
    if not username or "@" not in email:
        return login_redirect(request, "invalid_token")
    allowed_domain = os.getenv("SSO_AUTO_PROVISION_DOMAIN", "@itsr.co.th").strip().lower()
    if allowed_domain and not allowed_domain.startswith("@"):
        allowed_domain = "@" + allowed_domain
    if allowed_domain and not email.endswith(allowed_domain):
        return login_redirect(request, "not_allowed")

    display_name = " ".join(str(profile.get("name") or username).split())[:200]
    session_token = secrets.token_urlsafe(48)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=8)
    try:
        store.admin_action(session_token, "create_session", {
            "email": email,
            "username": username[:120],
            "name": display_name,
            "expires_at": expires_at.isoformat(),
        })
    except Exception:
        logger.exception("sso_session_create_failed")
        return login_redirect(request, "login_unavailable")

    target = clean_return_to(flow.get("return_to"))
    response = RedirectResponse(f"{base}{target}", status_code=302)
    set_auth_cookie(response, session_token, 8 * 60 * 60, base.startswith("https://"))
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/api/auth/me")
def auth_me(admin: dict = Depends(current_admin)) -> dict:
    return {"email": admin["email"], "username": admin["username"], "name": admin.get("name", ""),
            "expires_at": admin.get("expires_at")}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    if len(token) >= 32 and not token.startswith("flow."):
        try:
            store.admin_action(token, "revoke_session")
        except Exception:
            logger.exception("admin_logout_revoke_failed")
    response.delete_cookie(ADMIN_SESSION_COOKIE, path="/", samesite="lax")
    return {"ok": True}


@app.post("/api/chat/sessions")
def create_chat_session(response: Response, chat_guest: str | None = Cookie(default=None)) -> dict:
    guest = chat_guest or secrets.token_urlsafe(48)
    try:
        if not chat_guest:
            store.chat_action(guest, "create_guest")
        session = store.chat_action(guest, "create_session")
    except Exception:
        raise HTTPException(status_code=503, detail="chat_store_unavailable")
    if not chat_guest:
        response.set_cookie("chat_guest", guest, httponly=True, secure=os.getenv("VERCEL") == "1",
                            samesite="lax", max_age=60 * 60 * 24 * 365)
    return session


@app.get("/api/chat/sessions")
def list_chat_sessions(chat_guest: str | None = Cookie(default=None)) -> dict:
    try:
        guest = guest_or_401(chat_guest)
        return {"sessions": store.chat_action(guest, "list_sessions")}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="chat_store_unavailable")


@app.get("/api/chat/sessions/{session_id}/messages")
def list_chat_messages(session_id: str, chat_guest: str | None = Cookie(default=None)) -> dict:
    try:
        guest = guest_or_401(chat_guest)
        return {"messages": store.chat_action(guest, "load_session", valid_session(session_id))}
    except HTTPException:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise HTTPException(status_code=404, detail="chat_session_not_found")
        raise HTTPException(status_code=503, detail="chat_store_unavailable")
    except Exception:
        raise HTTPException(status_code=503, detail="chat_store_unavailable")


@app.get("/api/chat/sessions/{session_id}/media/{attachment_id}")
def get_chat_media(session_id: str, attachment_id: str,
                   chat_guest: str | None = Cookie(default=None)) -> dict:
    guest = guest_or_401(chat_guest)
    sid = valid_session(session_id)
    aid = valid_session(attachment_id)
    try:
        attachment = store.chat_action(guest, "get_attachment", sid, {"id": aid})
        if not attachment:
            raise HTTPException(status_code=404, detail="attachment_not_found")
        signed = store.signed_download(attachment["object_path"])
        return {"url": store.url + "/storage/v1" + signed["signedURL"],
                "mime_type": attachment["mime_type"]}
    except HTTPException:
        raise
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise HTTPException(status_code=404, detail="attachment_not_found")
        raise HTTPException(status_code=503, detail="media_unavailable")
    except Exception:
        raise HTTPException(status_code=503, detail="media_unavailable")


class UploadRequest(BaseModel):
    session_id: str
    filename: str = Field(max_length=200)
    mime_type: str
    byte_size: int = Field(gt=0, le=104857600)


@app.post("/api/chat/media/upload-url")
def create_upload_url(request: UploadRequest, chat_guest: str | None = Cookie(default=None)) -> dict:
    allowed = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
               "image/gif": ".gif", "video/mp4": ".mp4", "video/webm": ".webm",
               "video/quicktime": ".mov"}
    suffix = allowed.get(request.mime_type)
    if not suffix or not request.filename.lower().endswith(suffix):
        raise HTTPException(status_code=422, detail="unsupported_media_type")
    session_id = valid_session(request.session_id)
    try:
        guest = guest_or_401(chat_guest)
        store.chat_action(guest, "load_session", session_id)
        path = f"{session_id}/{uuid.uuid4()}{suffix}"
        signed = store.signed_upload(path)
        record = store.chat_action(guest, "save_attachment", session_id,
            {"object_path": path, "mime_type": request.mime_type, "byte_size": request.byte_size})
        return {"attachment_id": record["id"], "object_path": path,
                "upload_url": store.url + "/storage/v1" + signed["url"]}
    except Exception:
        raise HTTPException(status_code=503, detail="media_upload_unavailable")


class KeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=10, max_length=300)


class LlmConfigRequest(BaseModel):
    provider: Literal["gemini", "openrouter"]
    gemini_intent_model: str = Field(min_length=2, max_length=120)
    gemini_vision_model: str = Field(min_length=2, max_length=120)
    gemini_answer_model: str = Field(min_length=2, max_length=120)
    openrouter_intent_model: str = Field(min_length=2, max_length=120)
    openrouter_vision_model: str = Field(min_length=2, max_length=120)
    openrouter_answer_model: str = Field(min_length=2, max_length=120)


class OpenRouterKeyRequest(BaseModel):
    key: str = Field(min_length=10, max_length=500)


class PersonaRequest(BaseModel):
    length: Literal["short", "medium", "long"]
    tone: Literal["friendly", "normal", "very_polite"]
    voice: Literal["male", "female"]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog")
def catalog(admin: dict = Depends(current_admin)) -> dict:
    try:
        return get_catalog(store)
    except Exception:
        raise HTTPException(status_code=503, detail="knowledge_unavailable")


class PreferencesRequest(BaseModel):
    bubble_color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    chat_text_size: Literal["small", "medium", "large"]
    theme_preference: Literal["light", "dark"]


@app.get("/api/admin/dashboard")
def admin_dashboard(admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.admin_action(admin["token"], "dashboard")
    except Exception:
        raise HTTPException(status_code=503, detail="dashboard_unavailable")


@app.get("/api/admin/preferences")
def get_admin_preferences(admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.admin_action(admin["token"], "get_preferences")
    except Exception:
        raise HTTPException(status_code=503, detail="preferences_unavailable")


@app.post("/api/admin/preferences")
def save_admin_preferences(request: PreferencesRequest, admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.admin_action(admin["token"], "save_preferences", request.model_dump())
    except Exception:
        raise HTTPException(status_code=503, detail="preferences_save_failed")


@app.get("/api/admin/logs")
def admin_logs(limit: int = Query(default=50, ge=1, le=100),
               action: str | None = Query(default=None, max_length=100),
               admin: dict = Depends(current_admin)) -> dict:
    try:
        rows = store.admin_action(admin["token"], "list_logs", {"limit": limit, "action": action or ""})
        return {"logs": rows}
    except Exception:
        raise HTTPException(status_code=503, detail="logs_unavailable")


@app.post("/api/chat")
def chat(request: ChatRequest, chat_guest: str | None = Cookie(default=None)) -> dict:
    event = normalize_chat_input(request.event, request.question)
    plain_item_ids = event.metadata.get("plain_item_ids") is True
    question = ("ช่วยแนะนำสินค้าจากรหัสที่ส่งมา" if plain_item_ids else
                event.text or ("ช่วยดูสินค้าจากการ์ดนี้" if event.item_ids else ""))
    if not question and not request.attachment_ids and event.message_type not in ("faq_liveagent", "sticker", "notification", "order", "unknown"):
        raise HTTPException(status_code=422, detail="question must not be blank")

    if request.product_name and request.model_name:
        raise HTTPException(status_code=422, detail="Select either product_name or model_name")
    session_id = valid_session(request.session_id or "")
    try:
        guest = guest_or_401(chat_guest)
        prior = store.chat_action(guest, "load_session", session_id)
    except urllib.error.HTTPError as exc:
        if exc.code == 400:
            raise HTTPException(status_code=404, detail="chat_session_not_found")
        raise HTTPException(status_code=503, detail="chat_store_unavailable")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="chat_store_unavailable")
    history = [{"role": row["role"], "content": row["content"],
                "product_name": row.get("payload", {}).get("product_name")}
               for row in prior[-12:]]
    is_item_event = event.message_type in ("item", "variation_card", "bundle_message") and bool(event.item_ids)
    listings = []
    if is_item_event:
        for item_id in event.item_ids:
            try:
                listing = lookup_shopee_item(item_id)
                if listing:
                    listings.append(listing)
            except Exception:
                logger.exception("shopee_card_lookup_failed item_id=%s", item_id)
    descriptions = [""] * len(request.attachment_ids)
    pending = []
    for index, attachment_id in enumerate(request.attachment_ids):
        attachment = store.chat_action(guest, "get_attachment", session_id, {"id": valid_session(attachment_id)})
        if not attachment:
            raise HTTPException(status_code=422, detail="attachment_not_found")
        if attachment["status"] == "complete":
            descriptions[index] = attachment["vision_description"]
            continue
        pending.append((index, attachment_id, attachment))
    for start in range(0, len(pending), 5):
        batch = pending[start:start + 5]
        try:
            media = [(store.media_bytes(item[2]["object_path"]), item[2]["mime_type"]) for item in batch]
            config = store.runtime_config()
            if config.get("provider") == "openrouter":
                results = describe_openrouter_batch(media, question, config["openrouter_key"],
                                                    config["openrouter_vision_model"])
            else:
                results = describe_batch_with_key_rotation(media, question, store.next_key,
                    lambda items, prompt, key: describe_media_batch(items, prompt, key,
                        config.get("gemini_vision_model")))
            for (index, attachment_id, _), result in zip(batch, results):
                store.chat_action(guest, "update_vision", session_id,
                    {"id": attachment_id, "status": "complete", "model": result.model, "description": result.description})
                descriptions[index] = result.description
        except Exception as exc:
            logger.warning("vision_batch_failed error_type=%s error_code=%s", type(exc).__name__, getattr(exc, "code", None))
            for index, attachment_id, _ in batch:
                store.chat_action(guest, "update_vision", session_id,
                    {"id": attachment_id, "status": "failed", "description": ""})
                descriptions[index] = "[ไม่สามารถอ่านภาพหรือวิดีโอได้]"
    product_cards = [{"item_id": item["item_id"], "name": item["name"], "image_url": item["image_url"]}
                     for item in listings]
    user_content = request.question if plain_item_ids and request.question.strip() else question
    try:
        store.chat_action(guest, "save_message", session_id, {"role": "user", "content": user_content,
            "payload": {"message_type": event.message_type, "item_ids": list(event.item_ids),
                        "order_sn": event.order_sn, "media": list(event.media),
                        "attachment_ids": request.attachment_ids, "vision_descriptions": descriptions,
                        "product_cards": product_cards,
                        "card_title": product_cards[0]["name"] if len(product_cards) == 1 else None,
                        "image_url": product_cards[0]["image_url"] if len(product_cards) == 1 else None}})
    except Exception:
        raise HTTPException(status_code=503, detail="chat_save_failed")
    if is_item_event:
        if listings:
            knowledge = store.knowledge()
            matched_names = []
            for listing in listings:
                title = " ".join(listing["name"].lower().split())
                matches = [p["name"] for p in knowledge["products"]
                           if " ".join(p["name"].lower().split()) in title]
                matched_names.append(matches[0] if len(matches) == 1 else None)
            unique_models = list(dict.fromkeys(name for name in matched_names if name))
            for listing, matched in zip(listings, matched_names):
                store.chat_action(guest, "save_product", session_id, {"item_id": listing["item_id"],
                    "product_name": matched, "source": "customer_card",
                    "is_anchor": len(listings) == 1 or len(unique_models) == 1,
                    "card_snapshot": {"name": listing["name"]}})
            evidence = "\n\n".join(
                f"รหัสสินค้า: {listing['item_id']}\nชื่อสินค้า: {listing['name']}\nรายละเอียดสินค้า:\n{listing['description'][:5000]}"
                for listing in listings
            )
            model_label = " / ".join(unique_models) if unique_models else " / ".join(item["name"] for item in listings)
            if plain_item_ids:
                question = "จากสินค้าที่ส่งมา ช่วยบอกชื่อรุ่นและสรุปจุดเด่นที่ระบุในข้อมูลสินค้า"
            answer, _ = engine.answerer.answer(
                question, evidence, product_name=model_label, intent="product_info", history=history)
            found_ids = {listing["item_id"] for listing in listings}
            missing_ids = [item_id for item_id in event.item_ids if item_id not in found_ids]
            fallback = "พบสินค้า: " + "; ".join(item["name"] for item in listings)
            if missing_ids:
                fallback += f"\nส่วนรหัส {' และ '.join(missing_ids)} ยังไม่พบในรายการร้าน"
            reply = {"status": "answered", "answer": answer or fallback,
                     "product_name": unique_models[0] if len(unique_models) == 1 else None,
                     "item_id": listings[0]["item_id"] if len(listings) == 1 else None,
                     "source_type": "shopee_exact_item", "product_cards": product_cards}
        else:
            reply = {"status": "need_model", "answer": "ผมหาสินค้าจาก item_id ที่ส่งมาในรายการร้านไม่พบครับ ลองตรวจเลขอีกครั้งได้ไหมครับ",
                     "unresolved_item_ids": list(event.item_ids)}
    elif event.message_type == "faq_liveagent":
        settings = store.chat_action(guest, "live_agent_settings", session_id)
        if settings.get("action") == "bot_reply" and question:
            reply = engine.answer(question, history=history, session_id=session_id)
        else:
            record = store.escalate(session_id, "", question or "ลูกค้าขอคุยกับเจ้าหน้าที่", history)
            reply = {"status": "escalated" if record else "handoff_mock",
                     "answer": "บันทึกคำขอให้เจ้าหน้าที่ตรวจสอบแล้วครับ" if record else "ยังบันทึกคำขอไม่ได้ครับ กรุณาลองอีกครั้ง",
                     "case_id": record.get("id") if record else None, "handoff_sent": False}
    elif event.message_type in ("sticker", "notification", "order") and not question:
        reply = {"status": "answered", "answer": "ได้รับข้อมูลแล้วครับ พิมพ์สิ่งที่อยากให้ช่วยดูต่อได้เลยครับ"}
    elif event.message_type == "unknown":
        reply = {"status": "need_model", "answer": "ผมยังอ่านข้อมูลที่ส่งมาไม่ครบครับ ช่วยพิมพ์สิ่งที่อยากให้ดูอีกนิดได้ไหมครับ"}
    else:
        anchor = store.chat_action(guest, "product_anchor", session_id)
        anchored_name = anchor.get("product_name") if anchor else None
        reply = engine.answer(question or "ช่วยดูภาพหรือวิดีโอที่ส่งมา", product_name=request.product_name or anchored_name,
                              model_name=request.model_name, history=history, session_id=session_id,
                              vision_evidence=descriptions)
    try:
        store.chat_action(guest, "save_message", session_id, {"role": "assistant",
            "content": reply.get("answer", ""), "payload": reply})
        if reply.get("product_name"):
            explicit = reply["product_name"].casefold() in question.casefold()
            store.chat_action(guest, "save_product", session_id, {
                "product_name": reply["product_name"],
                "source": "customer_text" if explicit else "bot_suggestion",
                "is_anchor": explicit})
    except Exception:
        raise HTTPException(status_code=503, detail="chat_save_failed")
    return reply


@app.get("/api/llm/keys")
def list_llm_keys(admin: dict = Depends(current_admin)) -> dict:
    try:
        return {"keys": store.list_keys(admin["token"])}
    except Exception:
        raise HTTPException(status_code=503, detail="key_store_unavailable")


@app.get("/api/llm/config")
def get_llm_config(admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.llm_config_action("get_config", admin["token"])
    except Exception:
        raise HTTPException(status_code=503, detail="llm_config_unavailable")


@app.get("/api/llm/persona")
def get_llm_persona(admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.persona_action("get_config", admin["token"])
    except Exception:
        raise HTTPException(status_code=503, detail="persona_unavailable")


@app.post("/api/llm/persona")
def save_llm_persona(request: PersonaRequest, admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.save_persona(admin["token"], request.model_dump())
    except Exception:
        raise HTTPException(status_code=503, detail="persona_save_failed")


@app.post("/api/llm/config")
def save_llm_config(request: LlmConfigRequest, admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.save_llm_config(admin["token"], request.model_dump())
    except Exception:
        raise HTTPException(status_code=503, detail="llm_config_save_failed")


@app.post("/api/llm/openrouter-key")
def set_openrouter_key(request: OpenRouterKeyRequest, admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.set_openrouter_key(admin["token"], request.key)
    except Exception:
        raise HTTPException(status_code=503, detail="openrouter_key_save_failed")


@app.post("/api/llm/keys")
def add_llm_key(request: KeyRequest, admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.add_key(request.label, request.key, admin["token"])
    except Exception:
        raise HTTPException(status_code=503, detail="key_store_unavailable")


@app.delete("/api/llm/keys/{key_id}")
def delete_llm_key(key_id: int, admin: dict = Depends(current_admin)) -> dict:
    try:
        return store.delete_key(key_id, admin["token"])
    except Exception:
        raise HTTPException(status_code=503, detail="key_store_unavailable")
