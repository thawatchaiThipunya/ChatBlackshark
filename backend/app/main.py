from __future__ import annotations

import os
import hmac
import secrets
import uuid
import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Cookie, Depends, Header, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from .engine_v2 import ProductChatEngine, get_catalog
from .store import SupabaseStore
from .chat_inputs import normalize_chat_input
from .shopee_products import lookup_shopee_item
from .product_identity import match_catalog_product
from .admin_auth import ADMIN_SESSION_COOKIE, require_admin as require_admin_session
from .vision import describe_batch_with_key_rotation, describe_media_batch, describe_openrouter_batch
from .ai_usage import set_usage_reference, reset_usage_reference
from .history_context import recent_context
from .engine_tokens import (
    ACCESS_TOKEN_TTL_SECONDS,
    MIN_SECRET_LENGTH,
    issue_engine_access_token,
    verify_engine_access_token,
)
from . import legacy_data


app = FastAPI(
    title="Black Shark Product Chat API",
    version="1.0.0",
    description="Answers from the Supabase knowledge base selected by platform and shop.",
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
    # Keep Hub references opaque even when a caller supplies a custom request ID.
    usage_reference = set_usage_reference(secrets.token_hex(8))
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("request_failed %s %s request_id=%s", request.method, request.url.path, request_id)
        raise
    finally:
        reset_usage_reference(usage_reference)
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
    product_names: list[str] = Field(default_factory=list, max_length=50)
    card_title: str | None = Field(default=None, max_length=300)
    card_product_names: list[str] = Field(default_factory=list, max_length=10)


class ChatRequest(BaseModel):
    question: str = Field(default="", max_length=1000)
    event: dict | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    product_name: str | None = None
    model_name: str | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=100)
    request_id: str | None = Field(default=None, min_length=1, max_length=200)
    history: list[ChatTurn] = Field(default_factory=list, max_length=24)


class EnginePersonaPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bot_name: str = Field(default="ผู้ช่วยร้าน", min_length=1, max_length=80)
    persona_text: str = Field(default="", max_length=2000)
    friendliness: int = Field(default=50, ge=0, le=100)
    length: Literal["short", "medium", "long"] = "medium"
    detail_level: int = Field(default=50, ge=0, le=100)
    voice: Literal["male", "female"] = "male"


class TicketAnswerRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=200)
    message_id: str = Field(min_length=1, max_length=200)
    shop_id: str = Field(min_length=1, max_length=100)
    platform: str = Field(min_length=1, max_length=50)
    persona: EnginePersonaPayload | None = None


class LegacyEngineAnswerRequest(TicketAnswerRequest):
    request_id: str | None = Field(default=None, max_length=200)


class LegacyEngineTestAnswerRequest(BaseModel):
    question: str = Field(default="", max_length=2000)
    platform: str = Field(min_length=1, max_length=50)
    shop_id: str = Field(min_length=1, max_length=100)
    shop_name: str | None = Field(default=None, max_length=200)
    history: list[dict[str, Any]] = Field(default_factory=list, max_length=24)
    images: list[str] = Field(default_factory=list, max_length=10)
    item_ids: list[str] = Field(default_factory=list, max_length=10)
    order_sn: str | None = Field(default=None, max_length=100)
    persona: EnginePersonaPayload | None = None


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
def list_chat_messages(session_id: str, before_at: str | None = None,
                       before_id: str | None = None,
                       chat_guest: str | None = Cookie(default=None)) -> dict:
    try:
        guest = guest_or_401(chat_guest)
        if bool(before_at) != bool(before_id):
            raise HTTPException(status_code=422, detail="invalid_cursor")
        if before_at:
            try:
                datetime.fromisoformat(before_at.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=422, detail="invalid_cursor")
            valid_session(before_id or "")
        return store.chat_messages_page(guest, valid_session(session_id), before_at, before_id)
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
    bot_name: str = Field(min_length=1, max_length=80)
    persona_text: str = Field(max_length=2000)
    friendliness: int = Field(ge=0, le=100)
    length: Literal["short", "medium", "long"]
    detail_level: int = Field(ge=0, le=100)
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


class _ShopKnowledgeStore:
    """Per-request knowledge view; provider settings and keys remain shared."""
    def __init__(self, base: SupabaseStore, knowledge: dict[str, Any], scope: dict[str, Any],
                 persona_override: dict[str, Any] | None = None):
        self.base, self.data, self.scope = base, knowledge, scope
        self.persona_override = persona_override

    def knowledge(self) -> dict[str, Any]:
        return self.data

    def persona(self) -> dict[str, Any]:
        if self.persona_override is not None:
            return self.persona_override
        global_persona_shop = os.getenv("GLOBAL_PERSONA_SHOP_ID", "1191420560")
        global_persona_platform = os.getenv("GLOBAL_PERSONA_PLATFORM", "shopee")
        if self.scope["shop_id"] == global_persona_shop and self.scope["platform"] == global_persona_platform:
            return self.base.persona()
        return {"bot_name": "ผู้ช่วยร้าน", "persona_text": f"ตอบในนามร้าน {self.scope['shop_name']} เท่านั้น",
                "friendliness": 50, "length": "medium", "detail_level": 50, "voice": "male"}

    def __getattr__(self, name: str) -> Any:
        if name == "escalate":
            return None
        return getattr(self.base, name)


def _shop_context(platform: str, shop_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        scope, knowledge = store.knowledge_for_shop(platform, shop_id)
    except Exception as exc:
        logger.warning("ticket_shop_scope_unavailable type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="shop_scope_unavailable")
    if not scope:
        raise HTTPException(status_code=403, detail="shop_not_configured_for_knowledge")
    if not knowledge or not knowledge.get("products"):
        raise HTTPException(status_code=503, detail="shop_knowledge_not_ready")
    return scope, knowledge


def _ticket_conversation(conversation_id: str, shop_id: str, platform: str,
                         scopes: list[dict[str, Any]]) -> dict[str, Any]:
    conversation = legacy_data.get_conversation(conversation_id, shop_id, platform, scopes)
    if not conversation:
        raise HTTPException(status_code=404, detail="conversation_not_found_for_shop")
    return conversation


def _trusted_media_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        trusted = host.endswith(".supabase.co") or host.endswith(".susercontent.com") or host in {
            "cf.shopee.co.th", "img.sp.mms.shopee.sg", "down-th.img.susercontent.com",
            "down-vn.img.susercontent.com", "down-sg.img.susercontent.com",
            "deo.shopeemobile.com",
        }
        return parsed.scheme == "https" and trusted and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        return False


class _TrustedMediaRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_media_url(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_legacy_media(item: dict[str, str]) -> tuple[bytes, str]:
    url = str(item.get("url") or "")
    if not _trusted_media_url(url):
        raise ValueError("untrusted_media_host")
    request = urllib.request.Request(url, headers={"User-Agent": "BlackSharkSupport/1.0"})
    opener = urllib.request.build_opener(_TrustedMediaRedirect())
    with opener.open(request, timeout=20) as response:
        mime = (response.headers.get_content_type() or "").lower()
        allowed = {"image/jpeg", "image/png", "image/webp", "image/gif", "video/mp4", "video/webm", "video/quicktime"}
        if mime not in allowed:
            suffix = urlparse(response.geturl()).path.lower()
            mime = next((kind for ending, kind in ((".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                        (".png", "image/png"), (".webp", "image/webp"), (".gif", "image/gif"),
                        (".mp4", "video/mp4"), (".webm", "video/webm"), (".mov", "video/quicktime"))
                        if suffix.endswith(ending)), "")
        if mime not in allowed:
            raise ValueError("unsupported_media_type")
        max_size = 40 * 1024 * 1024 if mime.startswith("video/") else 15 * 1024 * 1024
        data = response.read(max_size + 1)
        if not data or len(data) > max_size:
            raise ValueError("media_size_invalid")
        return data, mime


def _describe_legacy_media(media_urls: list[dict[str, str]], question: str) -> list[str]:
    descriptions: list[str] = []
    unique = []
    seen: set[str] = set()
    for item in media_urls:
        url = str(item.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            unique.append(item)
    if len(unique) > 10:
        unique = unique[:10]
    for start in range(0, len(unique), 5):
        batch = unique[start:start + 5]
        try:
            media = [_download_legacy_media(item) for item in batch]
            config = store.runtime_config()
            if config.get("provider") == "openrouter":
                results = describe_openrouter_batch(media, question,
                    config.get("openrouter_key", ""), config.get("openrouter_vision_model", ""))
            else:
                results = describe_batch_with_key_rotation(media, question, store.next_key,
                    lambda items, prompt, key: describe_media_batch(items, prompt, key,
                        config.get("gemini_vision_model")), key_cycle=store.key_cycle)
            descriptions.extend(result.description for result in results)
        except Exception as exc:
            logger.warning("ticket_vision_failed batch=%s type=%s", start // 5 + 1, type(exc).__name__)
            descriptions.extend([""] * len(batch))
    return descriptions


def _extract_order_number(text: str) -> str:
    match = re.search(r"(?<![A-Z0-9])[A-Z0-9-]{8,24}(?![A-Z0-9])", text.upper())
    return match.group(0) if match else ""


def _answer_legacy_message(request: TicketAnswerRequest) -> dict[str, Any]:
    scope, knowledge = _shop_context(request.platform, request.shop_id)
    try:
        message = legacy_data.get_inbound_message(request.conversation_id, request.message_id,
            request.shop_id, scope["platform"], [scope])
    except Exception as exc:
        logger.warning("ticket_message_read_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="legacy_message_unavailable")
    if not message:
        raise HTTPException(status_code=404, detail="inbound_message_not_found")
    conversation = legacy_data.get_conversation(request.conversation_id, request.shop_id,
                                               scope["platform"], [scope])
    if not conversation:
        raise HTTPException(status_code=404, detail="conversation_not_found_for_shop")
    legacy_shop_name = str(conversation.get("legacy_shop_name") or "")
    event = message["event"]
    plain_ids = event.metadata.get("plain_item_ids") is True
    question = ("ช่วยแนะนำสินค้าจากรหัสที่ส่งมา" if plain_ids else event.text or
                ("ช่วยดูสินค้าจากการ์ดนี้" if event.item_ids else
                 "ช่วยดูภาพหรือวิดีโอที่ส่งมา" if message.get("media") else
                 f"ช่วยเช็กสถานะคำสั่งซื้อ {event.order_sn}" if event.message_type == "order" and event.order_sn else ""))
    if event.message_type == "faq_liveagent":
        return {"status": "handoff_required", "bot_status": "handoff_mock", "intent": "store_support",
                "reason": "customer_requested_agent", "answer": "ลูกค้าขอคุยกับเจ้าหน้าที่ครับ",
                "handoff_required": True, "handoff_reason": "customer_requested_agent",
                "conversation_id": request.conversation_id, "message_id": request.message_id,
                "history_pair_count": 0, "image_description": "", "product_cards": [], "sources": []}
    if event.message_type == "order" and not event.order_sn and not event.text:
        return {"status": "need_model", "bot_status": "need_model", "intent": "order_status",
                "reason": "order_reference_missing", "answer": "ผมยังอ่านเลขจากการ์ดคำสั่งซื้อนี้ไม่ได้ครับ รบกวนส่งเลขคำสั่งซื้อมาอีกครั้งได้ไหมครับ",
                "handoff_required": False, "conversation_id": request.conversation_id,
                "message_id": request.message_id, "history_pair_count": 0,
                "image_description": "", "product_cards": [], "sources": []}
    if event.message_type in {"sticker", "notification", "rating_card"} and not question:
        received_reason = {"sticker": "sticker_received", "notification": "notification_received",
                           "rating_card": "rating_card_received"}[event.message_type]
        return {"status": "answered", "bot_status": "answered", "intent": "platform_event", "reason": received_reason,
                "answer": "ได้รับข้อมูลแล้วครับ มีอะไรให้ช่วยดูต่อได้เลยครับ",
                "handoff_required": False, "conversation_id": request.conversation_id,
                "message_id": request.message_id, "history_pair_count": 0,
                "image_description": "", "product_cards": [], "sources": []}
    if event.message_type == "unknown":
        return {"status": "handoff_required", "bot_status": "need_model", "intent": "unknown",
                "reason": "unsupported_input_type", "answer": "รูปแบบข้อความนี้ยังอ่านไม่ครบครับ ขอให้เจ้าหน้าที่ช่วยดูต่อ",
                "handoff_required": True, "handoff_reason": "unsupported_input_type",
                "conversation_id": request.conversation_id, "message_id": request.message_id,
                "history_pair_count": 0, "image_description": "", "product_cards": [], "sources": []}

    try:
        try:
            alias_is_unique = (legacy_data.shop_alias_is_unique(scope["platform"], request.shop_id,
                                                                 legacy_shop_name)
                               if scope["platform"] == "shopee" else False)
        except Exception as exc:
            logger.warning("ticket_shop_alias_check_failed type=%s", type(exc).__name__)
            alias_is_unique = False
        legacy_commerce_is_scoped = scope["platform"] == "shopee" and alias_is_unique
        history = legacy_data.history_for_conversation(request.conversation_id, request.shop_id,
            scope["platform"], legacy_shop_name, before=message.get("created_timestamp"), max_pairs=5)
        catalog_products = knowledge["products"]
        for turn in history:
            if turn.get("role") != "user":
                continue
            names = turn.get("card_product_names") or turn.get("product_names") or []
            kb_names = list(dict.fromkeys(
                name for raw_name in names
                if isinstance(raw_name, str)
                if (name := match_catalog_product(raw_name, catalog_products))
            ))
            turn["product_name"] = kb_names[0] if len(kb_names) == 1 else None
            turn["product_names"] = kb_names
            turn["card_product_names"] = kb_names
            turn["card_title"] = kb_names[0] if len(kb_names) == 1 else ""
            if isinstance(turn.get("product_scope"), dict):
                turn["product_scope"]["product_names"] = kb_names
        prior_users = [turn for turn in history if turn.get("role") == "user"]
        for turn in prior_users[-2:]:
            names = turn.get("card_product_names") or turn.get("product_names") or []
            turn["product_scope"] = {"question": turn.get("content", ""), "product_names": names[:10]}
        card_lookup_unavailable = False
        try:
            card_map = (legacy_data.product_identities_by_ids(list(event.item_ids), legacy_shop_name)
                        if legacy_commerce_is_scoped else {})
        except Exception as exc:
            logger.warning("ticket_current_card_lookup_failed type=%s", type(exc).__name__)
            card_map = {}
            card_lookup_unavailable = True
        identity_rows = list(card_map.values())
        for listing in identity_rows:
            listing["product_name"] = match_catalog_product(listing["name"], catalog_products)
        missing_item_ids = [item_id for item_id in event.item_ids if item_id not in card_map]
        unmatched_item_ids = [item_id for item_id in event.item_ids
                              if item_id in card_map and not card_map[item_id].get("product_name")]
        listings = [{"item_id": item_id, "name": card_map[item_id]["product_name"],
                     "product_name": card_map[item_id]["product_name"]}
                    for item_id in event.item_ids if item_id in card_map and card_map[item_id].get("product_name")]
        vision_descriptions = _describe_legacy_media(message.get("media") or [], question)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("ticket_context_read_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="legacy_context_unavailable")

    if event.item_ids and (missing_item_ids or unmatched_item_ids or card_lookup_unavailable):
        reason = ("card_lookup_unavailable" if card_lookup_unavailable or not legacy_commerce_is_scoped
                  else "item_id_not_found" if missing_item_ids else "card_not_in_shop_kb")
        return {"status": "handoff_required", "bot_status": "handoff_mock",
                "intent": "product_info", "reason": reason,
                "answer": ("ตอนนี้ค้นข้อมูลการ์ดสินค้าจากระบบร้านไม่ได้ครับ ขอให้เจ้าหน้าที่ช่วยตรวจสอบก่อน"
                           if reason == "card_lookup_unavailable" else
                           "ผมยังยืนยันข้อมูลสินค้าจากรายการที่ส่งมาใน KB ของร้านนี้ไม่ได้ครับ ขอให้เจ้าหน้าที่ช่วยตรวจสอบก่อน"),
                "handoff_required": True, "handoff_reason": reason,
                "conversation_id": request.conversation_id, "message_id": request.message_id,
                "history_pair_count": min(5, (len(history) + 1) // 2), "image_description": "\n".join(
                    value for value in vision_descriptions if value), "image_descriptions": vision_descriptions,
                "unresolved_item_ids": list(dict.fromkeys(missing_item_ids + unmatched_item_ids)),
                "product_cards": [], "sources": []}

    scoped_store = _ShopKnowledgeStore(
        store, knowledge, scope,
        request.persona.model_dump() if request.persona is not None else None,
    )
    shop_engine = ProductChatEngine(store=scoped_store)
    customer_id = str(conversation.get("customer_id") or "").strip()

    def commerce_lookup(intent: str, product_names: list[str], card_context: list[dict[str, Any]],
                        product_mentions: list[str] | None = None) -> dict[str, Any]:
        if intent == "stock_availability":
            return {"unavailable": True}
        if intent not in {"order_status", "troubleshooting"}:
            return {}
        if not legacy_commerce_is_scoped or not customer_id:
            return {"unavailable": True}
        try:
            if intent == "troubleshooting":
                # Orders provide identity/context only. Product troubleshooting facts still come from this shop's KB/FAQ.
                if product_names or card_context:
                    return {}
                return {"orders": legacy_data.list_customer_orders(customer_id, legacy_shop_name, 20)}

            order_sn = event.order_sn or _extract_order_number(question)
            if not order_sn:
                for turn in reversed(history):
                    if turn.get("role") != "user":
                        continue
                    order_sn = _extract_order_number(str(turn.get("content") or ""))
                    if order_sn:
                        break
            if order_sn:
                order = legacy_data.find_customer_order(customer_id, legacy_shop_name, order_sn)
                return {"orders": [order] if order else []}

            orders = legacy_data.list_customer_orders(customer_id, legacy_shop_name, 20)
            if not orders:
                return {"orders": []}
            requested = set(product_names)
            requested.update(name for mention in (product_mentions or [])
                             if isinstance(mention, str)
                             if (name := match_catalog_product(mention, catalog_products)))
            if requested:
                matched_orders = []
                for order in orders:
                    item_names = {
                        name for item in order.get("items", []) if isinstance(item, dict)
                        for raw_name in (item.get("model_name"), item.get("name"))
                        if isinstance(raw_name, str)
                        if (name := match_catalog_product(raw_name, catalog_products))
                    }
                    if requested & item_names:
                        matched_orders.append(order)
                orders = matched_orders
            if len(orders) == 1:
                return {"orders": orders}
            return {"orders": [], "needs_clarification": len(orders) > 1}
        except Exception as exc:
            logger.warning("legacy customer order lookup failed type=%s", type(exc).__name__)
            return {"unavailable": True}

    if not question:
        question = "ช่วยอ่านข้อความที่ส่งมาและตอบในขอบเขตข้อมูลร้านนี้"
    try:
        reply = shop_engine.answer(question, history=history,
            product_name=listings[0].get("product_name") if len(listings) == 1 else None,
            card_context=listings, vision_evidence=vision_descriptions,
            commerce_lookup=commerce_lookup, store_label=scope["shop_name"])
    except Exception as exc:
        logger.warning("ticket_answer_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="answer_engine_unavailable")
    bot_status = str(reply.get("status") or "handoff_mock")
    handoff_required = (bot_status in {"handoff_mock", "escalated", "temporarily_unavailable", "key_required"}
                        or (bot_status == "need_model" and reply.get("reason") == "vision_unavailable")
                        or reply.get("handoff_sent") is True)
    reply.update({"bot_status": bot_status, "status": "handoff_required" if handoff_required else bot_status,
                  "handoff_required": handoff_required,
                  "handoff_reason": reply.get("reason") if handoff_required else None,
                  "conversation_id": request.conversation_id, "message_id": request.message_id,
                  "history_pair_count": min(5, (len(history) + 1) // 2),
                  "image_description": "\n".join(value for value in vision_descriptions if value),
                  "image_descriptions": vision_descriptions,
                  "unresolved_item_ids": [item_id for item_id in event.item_ids if item_id not in card_map]})
    return reply


@app.get("/api/tickets/shops")
def ticket_shops(admin: dict = Depends(current_admin)) -> dict[str, Any]:
    try:
        scopes = store.shop_scopes()
        return {"shops": [{key: scope[key] for key in ("platform", "platform_name", "shop_id", "shop_name", "files")}
                           for scope in scopes]}
    except Exception as exc:
        logger.warning("ticket_shops_unavailable type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="shop_scope_unavailable")


@app.get("/api/tickets/conversations")
def ticket_conversations(platform: str = Query(..., min_length=1, max_length=50),
                         shop_id: str = Query(..., min_length=1, max_length=100),
                         q: str = Query(default="", max_length=100),
                         page: int = Query(default=1, ge=1), limit: int = Query(default=60, ge=1, le=100),
                         admin: dict = Depends(current_admin)) -> dict[str, Any]:
    try:
        scopes = store.shop_scopes()
        return legacy_data.list_conversations(allowed_shops=scopes, query=q, shop_id=shop_id,
                                              platform=platform, page=page, limit=limit)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("ticket_conversations_unavailable type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="legacy_conversations_unavailable")


@app.get("/api/tickets/conversations/{conversation_id}/messages")
def ticket_messages(conversation_id: str, platform: str = Query(..., min_length=1, max_length=50),
                    shop_id: str = Query(..., min_length=1, max_length=100),
                    before_at: str | None = Query(default=None, max_length=80),
                    before_id: str | None = Query(default=None, max_length=200),
                    limit: int = Query(default=100, ge=1, le=100),
                    admin: dict = Depends(current_admin)) -> dict[str, Any]:
    _shop_context(platform, shop_id)
    try:
        return legacy_data.list_messages(conversation_id, shop_id, platform,
            [{"platform": platform.casefold(), "shop_id": shop_id,
              "shop_name": store.resolve_shop_scope(platform, shop_id)["shop_name"]}],
            before_at=before_at, before_id=before_id, limit=limit)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("ticket_messages_unavailable type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="legacy_messages_unavailable")


@app.get("/api/tickets/conversations/{conversation_id}/details")
def ticket_conversation_details(conversation_id: str, platform: str = Query(..., min_length=1, max_length=50),
                                shop_id: str = Query(..., min_length=1, max_length=100),
                                admin: dict = Depends(current_admin)) -> dict[str, Any]:
    scope, _ = _shop_context(platform, shop_id)
    try:
        conversation = _ticket_conversation(conversation_id, shop_id, scope["platform"], [scope])
        shop_scopes = store.shop_scopes()
        name_is_unique = sum(1 for candidate in shop_scopes if candidate["platform"] == scope["platform"]
                             and candidate["shop_name"].casefold() == scope["shop_name"].casefold()) == 1
        legacy_name = str(conversation.get("legacy_shop_name") or "")
        orders_unavailable = False
        try:
            legacy_alias_is_unique = legacy_data.shop_alias_is_unique(scope["platform"], shop_id, legacy_name)
            orders = (legacy_data.list_customer_orders(str(conversation.get("customer_id") or ""), legacy_name, 10)
                      if scope["platform"] == "shopee" and name_is_unique and legacy_alias_is_unique else [])
        except Exception as exc:
            logger.warning("ticket_customer_orders_failed type=%s", type(exc).__name__)
            orders, orders_unavailable = [], True
        return {"conversation": conversation, "shop": {key: scope[key] for key in
                ("platform", "platform_name", "shop_id", "shop_name")},
                "orders": orders, "orders_unavailable": orders_unavailable}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("ticket_details_unavailable type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="legacy_customer_details_unavailable")


@app.post("/api/tickets/answer")
def ticket_answer(request: TicketAnswerRequest, admin: dict = Depends(current_admin)) -> dict[str, Any]:
    return _answer_legacy_message(request)


def _engine_bearer(authorization: str | None) -> str:
    supplied = authorization or ""
    return supplied[7:].strip() if supplied[:7].lower() == "bearer " else ""


def _authorize_engine_request(authorization: str | None) -> None:
    bearer = _engine_bearer(authorization)
    expected = os.getenv("LEGACY_ENGINE_TOKEN", "")
    if expected and len(expected) >= MIN_SECRET_LENGTH and hmac.compare_digest(expected, bearer):
        return
    signing_secret = os.getenv("CHATBLACKSHARK_TOKEN_SIGNING_SECRET", "")
    if signing_secret and verify_engine_access_token(bearer, signing_secret):
        return
    raise HTTPException(status_code=401, detail="legacy_engine_unauthorized")


@app.post("/api/engine/token")
def issue_engine_token(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    issuer_secret = os.getenv("CHATBLACKSHARK_TOKEN_ISSUER_SECRET", "")
    signing_secret = os.getenv("CHATBLACKSHARK_TOKEN_SIGNING_SECRET", "")
    if len(issuer_secret) < MIN_SECRET_LENGTH:
        raise HTTPException(status_code=503, detail="engine_token_issuer_not_configured")
    if len(signing_secret) < MIN_SECRET_LENGTH:
        raise HTTPException(status_code=503, detail="engine_token_signing_not_configured")
    bearer = _engine_bearer(authorization)
    if not bearer or not hmac.compare_digest(issuer_secret, bearer):
        raise HTTPException(status_code=401, detail="engine_token_issuer_unauthorized")
    return {"access_token": issue_engine_access_token(signing_secret),
            "token_type": "Bearer", "expires_in": ACCESS_TOKEN_TTL_SECONDS}


@app.post("/api/engine/answer")
def legacy_engine_answer(request: LegacyEngineAnswerRequest,
                         authorization: str | None = Header(default=None)) -> dict[str, Any]:
    _authorize_engine_request(authorization)
    reply = _answer_legacy_message(request)
    return {**reply, "source": "chatblackshark", "products": reply.get("product_cards") or [],
            "image_desc": reply.get("image_description") or "", "request_id": request.request_id}


@app.post("/api/engine/test-answer")
def legacy_engine_test_answer(request: LegacyEngineTestAnswerRequest,
                              authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Run the same scoped v4 answer engine for test pages without a legacy message row."""
    _authorize_engine_request(authorization)

    scope, knowledge = _shop_context(request.platform, request.shop_id)
    question = request.question.strip() or ("ช่วยดูสินค้าจากการ์ดนี้" if request.item_ids else
        "ช่วยดูภาพหรือวิดีโอที่ส่งมา" if request.images else "")
    if not question:
        raise HTTPException(status_code=422, detail="question_required")

    history: list[dict[str, Any]] = []
    for turn in request.history[-24:]:
        content = str(turn.get("content") or turn.get("text") or "").strip()
        if not content:
            continue
        role = "assistant" if str(turn.get("role") or "").casefold() in {"assistant", "model", "bot"} else "user"
        safe_turn: dict[str, Any] = {"role": role, "content": content[:4000]}
        for key in ("product_name", "product_names", "card_title", "card_product_names", "image_desc", "vision_descriptions"):
            if key in turn:
                safe_turn[key] = turn[key]
        history.append(safe_turn)

    vision_descriptions = _describe_legacy_media(
        [{"url": url, "type": "image"} for url in request.images if url], question
    ) if request.images else []
    catalog = knowledge.get("products", [])
    alias = ""
    commerce_is_scoped = False
    if request.item_ids:
        try:
            alias = legacy_data.resolve_legacy_shop_name(scope["platform"], request.shop_id) or ""
            commerce_is_scoped = (scope["platform"] == "shopee" and bool(alias) and
                legacy_data.shop_alias_is_unique(scope["platform"], request.shop_id, alias))
        except Exception as exc:
            logger.warning("engine_test_shop_alias_check_failed type=%s", type(exc).__name__)

    card_rows: list[dict[str, Any]] = []
    if request.item_ids and commerce_is_scoped:
        try:
            card_map = legacy_data.product_identities_by_ids(request.item_ids, alias)
            card_rows = [card_map[item_id] for item_id in request.item_ids if item_id in card_map]
        except Exception as exc:
            logger.warning("engine_test_card_lookup_failed type=%s", type(exc).__name__)
    if request.item_ids and not commerce_is_scoped:
        return {"status": "handoff_required", "bot_status": "temporarily_unavailable",
            "intent": "product_info", "reason": "card_lookup_unavailable",
            "answer": "ตอนนี้ค้นข้อมูลการ์ดสินค้าจากระบบร้านไม่ได้ครับ ขอให้เจ้าหน้าที่ช่วยตรวจสอบก่อน",
            "handoff_required": True, "handoff_reason": "card_lookup_unavailable",
            "product_cards": [], "products": [], "sources": []}
    if request.item_ids and not card_rows:
        return {"status": "handoff_required", "bot_status": "handoff_mock", "intent": "product_info",
            "reason": "item_id_not_found",
            "answer": "ผมหาสินค้าจาก item_id ที่ส่งมาในรายการร้านไม่พบครับ ขอให้เจ้าหน้าที่ช่วยตรวจสอบก่อน",
            "handoff_required": True, "handoff_reason": "item_id_not_found",
            "product_cards": [], "products": [], "sources": [], "unresolved_item_ids": request.item_ids}
    for card in card_rows:
        card["product_name"] = match_catalog_product(str(card.get("name") or ""), catalog)
    unmatched_ids = [item_id for item_id in request.item_ids
                     if not next((row.get("product_name") for row in card_rows
                                  if row.get("item_id") == item_id), None)]
    if unmatched_ids:
        return {"status": "handoff_required", "bot_status": "handoff_mock", "intent": "product_info",
            "reason": "card_not_in_shop_kb",
            "answer": "ผมยังยืนยันข้อมูลสินค้าจากรายการที่ส่งมาใน KB ของร้านนี้ไม่ได้ครับ ขอให้เจ้าหน้าที่ช่วยตรวจสอบก่อน",
            "handoff_required": True, "handoff_reason": "card_not_in_shop_kb",
            "product_cards": [], "products": [], "sources": [], "unresolved_item_ids": unmatched_ids}
    kb_card_rows = [{"item_id": row["item_id"], "name": row["product_name"],
                     "product_name": row["product_name"]} for row in card_rows]

    def commerce_lookup(intent: str, product_names: list[str], card_context: list[dict[str, Any]],
                        product_mentions: list[str] | None = None) -> dict[str, Any]:
        if intent in {"order_status", "stock_availability"}:
            return {"unavailable": True}
        return {}

    try:
        scoped_store = _ShopKnowledgeStore(
            store, knowledge, scope,
            request.persona.model_dump() if request.persona is not None else None,
        )
        scoped_engine = ProductChatEngine(store=scoped_store)
        reply = scoped_engine.answer(
            question, history=history, product_name=kb_card_rows[0]["product_name"] if len(kb_card_rows) == 1 else None,
            card_context=kb_card_rows, vision_evidence=vision_descriptions,
            commerce_lookup=commerce_lookup, store_label=str(scope.get("shop_name") or "ร้านนี้"),
        )
    except Exception as exc:
        logger.warning("engine_test_answer_failed type=%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="answer_engine_unavailable")

    bot_status = str(reply.get("status") or "handoff_mock")
    handoff_required = (bot_status in {"handoff_mock", "escalated", "temporarily_unavailable", "key_required"}
        or (bot_status == "need_model" and reply.get("reason") == "vision_unavailable")
        or reply.get("handoff_sent") is True)
    cards = reply.get("products") if isinstance(reply.get("products"), list) else []
    return {**reply, "source": "chatblackshark", "products": cards, "product_cards": cards,
        "bot_status": bot_status, "status": "handoff_required" if handoff_required else bot_status,
        "handoff_required": handoff_required,
        "handoff_reason": reply.get("reason") if handoff_required else None,
        "image_description": "\n".join(value for value in vision_descriptions if value),
        "image_descriptions": vision_descriptions,
        "image_desc": "\n".join(value for value in vision_descriptions if value)}


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
    if not question and not request.attachment_ids and event.message_type not in (
            "faq_liveagent", "sticker", "notification", "order", "rating_card", "unknown"):
        raise HTTPException(status_code=422, detail="question must not be blank")

    if request.product_name and request.model_name:
        raise HTTPException(status_code=422, detail="Select either product_name or model_name")
    session_id = valid_session(request.session_id or "")
    turn_id = (request.request_id or "").strip() or secrets.token_hex(16)
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
    if request.request_id:
        completed = next((row.get("payload") for row in reversed(prior)
                          if row.get("role") == "assistant"
                          and isinstance(row.get("payload"), dict)
                          and row["payload"].get("request_id") == turn_id
                          and isinstance(row["payload"].get("answer"), str)), None)
        if completed:
            return completed
    platform = os.getenv("GLOBAL_PERSONA_PLATFORM", "shopee").strip().casefold()
    shop_id = os.getenv("GLOBAL_PERSONA_SHOP_ID", "1191420560").strip()
    scope, scoped_knowledge = _shop_context(platform, shop_id)
    scoped_store = _ShopKnowledgeStore(store, scoped_knowledge, scope)
    shop_engine = ProductChatEngine(store=scoped_store)
    is_item_event = event.message_type in ("item", "variation_card", "bundle_message") and bool(event.item_ids)
    history, product_scopes = recent_context(prior)
    user_turns = [turn for turn in history if turn["role"] == "user"]
    for turn, scope in zip(user_turns[-2:], product_scopes):
        turn["product_scope"] = scope
    listings = []
    if is_item_event:
        for item_id in event.item_ids:
            try:
                listing = lookup_shopee_item(item_id, platform=scope["platform"],
                                             shop_id=scope["shop_id"], shop_name=scope["shop_name"])
                if listing:
                    listings.append(listing)
            except Exception:
                logger.exception("shopee_card_lookup_failed item_id=%s", item_id)
    stored_cards: list[dict] = []
    stored_card_index = -1
    for index, row in enumerate(prior):
        if row.get("role") != "user":
            continue
        payload = row.get("payload") or {}
        cards = payload.get("product_cards") if isinstance(payload.get("product_cards"), list) else []
        if cards:
            stored_card_index = index
            stored_cards = [card for card in cards if isinstance(card, dict)]
    catalog_products = scoped_knowledge.get("products", [])
    for listing in listings:
        listing["product_name"] = match_catalog_product(listing["name"], catalog_products)
    prior_card_context = []
    for card in stored_cards:
        prior_name = str(card.get("name") or card.get("product_name") or "")
        product_name = match_catalog_product(prior_name, catalog_products)
        if not product_name and str(card.get("product_name") or "") in {row["name"] for row in catalog_products}:
            product_name = str(card["product_name"])
        if product_name:
            prior_card_context.append({"item_id": str(card.get("item_id") or "")[:100],
                                       "name": product_name, "product_name": product_name})
    found_item_ids = {str(row.get("item_id") or "") for row in listings}
    unresolved_card_ids = [item_id for item_id in event.item_ids
                           if item_id not in found_item_ids or not next(
                               (row.get("product_name") for row in listings if str(row.get("item_id")) == item_id), None)]
    current_card_context = ([{"item_id": str(row["item_id"]), "name": row["product_name"],
                              "product_name": row["product_name"]}
                             for row in listings if row.get("product_name")]
                            if is_item_event else prior_card_context)
    card_models = list(dict.fromkeys(card["product_name"] for card in current_card_context if card.get("product_name")))
    product_anchor = None
    if not is_item_event:
        try:
            product_anchor = store.chat_action(guest, "product_anchor", session_id)
        except Exception:
            product_anchor = None
    anchored_name = None
    if stored_card_index >= 0 and not is_item_event:
        later_names = [row.get("payload", {}).get("product_name") for row in prior[stored_card_index + 1:]
                       if isinstance(row.get("payload"), dict) and row.get("payload", {}).get("product_name")]
        anchored_name = later_names[-1] if later_names else (card_models[0] if len(card_models) == 1 else None)
    elif len(card_models) == 1:
        anchored_name = card_models[0]
    elif not current_card_context and product_anchor:
        anchored_name = product_anchor.get("product_name")
    if not current_card_context and not anchored_name:
        last_reply = prior[-1] if prior and prior[-1].get("role") == "assistant" else {}
        last_name = (last_reply.get("payload") or {}).get("product_name")
        if last_name in {item["name"] for item in catalog_products}:
            anchored_name = last_name
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
                        config.get("gemini_vision_model")), key_cycle=store.key_cycle)
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
    product_cards = [{"item_id": item["item_id"], "name": item["product_name"],
                      "product_name": item["product_name"]}
                     for item in listings if item.get("product_name")]

    def public_chat_commerce_lookup(intent: str, product_names: list[str],
                                    card_context: list[dict[str, Any]],
                                    product_mentions: list[str] | None = None) -> dict[str, Any]:
        return {"unavailable": True} if intent in {"order_status", "stock_availability"} else {}

    user_content = request.question if plain_item_ids and request.question.strip() else question
    try:
        store.chat_action(guest, "save_message", session_id, {"role": "user", "content": user_content,
            "payload": {"message_type": event.message_type, "item_ids": list(event.item_ids),
                        "request_id": turn_id,
                        "order_sn": event.order_sn, "media": list(event.media),
                        "attachment_ids": request.attachment_ids, "vision_descriptions": descriptions,
                        "product_cards": product_cards,
                        "product_name": product_cards[0].get("product_name") if len(product_cards) == 1 else None,
                        "card_title": product_cards[0]["name"] if len(product_cards) == 1 else None,
                        "image_url": product_cards[0].get("image_url") if len(product_cards) == 1 else None}})
    except Exception:
        raise HTTPException(status_code=503, detail="chat_save_failed")
    if is_item_event:
        if unresolved_card_ids:
            reply = {"status": "handoff_required", "intent": "product_info",
                     "reason": "card_not_in_shop_kb", "answer": "ผมยังยืนยันข้อมูลสินค้าจากรายการที่ส่งมาใน KB ของร้านนี้ไม่ได้ครับ ขอให้เจ้าหน้าที่ช่วยตรวจสอบก่อน",
                     "handoff_required": True, "handoff_reason": "card_not_in_shop_kb",
                     "unresolved_item_ids": unresolved_card_ids, "product_cards": []}
        elif listings:
            unique_models = list(dict.fromkeys(item.get("product_name") for item in listings if item.get("product_name")))
            for listing in listings:
                store.chat_action(guest, "save_product", session_id, {"item_id": listing["item_id"],
                    "product_name": listing.get("product_name"), "source": "customer_card",
                    "is_anchor": len(listings) == 1 or len(unique_models) == 1,
                    "card_snapshot": {"name": listing["product_name"]}})
            if plain_item_ids:
                question = "ช่วยสรุปชื่อสินค้าและจุดเด่นจากรายการสินค้าที่ส่งมา"
            reply = shop_engine.answer(question, product_name=unique_models[0] if len(unique_models) == 1 else None,
                                  history=history, session_id=session_id,
                                  vision_evidence=descriptions, card_context=current_card_context,
                                  commerce_lookup=public_chat_commerce_lookup)
            reply.update({"product_name": unique_models[0] if len(unique_models) == 1 else reply.get("product_name"),
                          "item_id": listings[0]["item_id"] if len(listings) == 1 else None,
                          "source_type": "shopee_exact_item", "product_cards": product_cards})
        else:
            reply = {"status": "need_model", "answer": "ผมหาสินค้าจาก item_id ที่ส่งมาในรายการร้านไม่พบครับ ลองตรวจเลขอีกครั้งได้ไหมครับ",
                     "unresolved_item_ids": list(event.item_ids)}
    elif event.message_type == "faq_liveagent":
        settings = store.chat_action(guest, "live_agent_settings", session_id)
        if settings.get("action") == "bot_reply" and question:
            reply = shop_engine.answer(question, history=history, session_id=session_id)
        else:
            record = store.escalate(session_id, "", question or "ลูกค้าขอคุยกับเจ้าหน้าที่", history)
            reply = {"status": "escalated" if record else "handoff_mock",
                     "answer": "บันทึกคำขอให้เจ้าหน้าที่ตรวจสอบแล้วครับ" if record else "ยังบันทึกคำขอไม่ได้ครับ กรุณาลองอีกครั้ง",
                     "case_id": record.get("id") if record else None, "handoff_sent": False}
    elif event.message_type in ("sticker", "notification", "order", "rating_card") and not question:
        reply = {"status": "answered", "answer": "ได้รับข้อมูลแล้วครับ พิมพ์สิ่งที่อยากให้ช่วยดูต่อได้เลยครับ"}
    elif event.message_type == "unknown":
        reply = {"status": "need_model", "answer": "ผมยังอ่านข้อมูลที่ส่งมาไม่ครบครับ ช่วยพิมพ์สิ่งที่อยากให้ดูอีกนิดได้ไหมครับ"}
    else:
        if product_scopes and product_scopes[-1].get("category"):
            anchored_name = None
        reply = shop_engine.answer(question or "ช่วยดูภาพหรือวิดีโอที่ส่งมา", product_name=request.product_name or anchored_name,
                              model_name=request.model_name, history=history, session_id=session_id,
                              vision_evidence=descriptions, card_context=current_card_context,
                              commerce_lookup=public_chat_commerce_lookup)
    try:
        reply["request_id"] = turn_id
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


@app.get("/api/llm/models")
def get_llm_models(provider: Literal["gemini", "openrouter"] = Query(...),
                   admin: dict = Depends(current_admin)) -> dict:
    try:
        return {"provider": provider, "models": store.model_catalog(provider)}
    except Exception:
        raise HTTPException(status_code=503, detail="model_catalog_unavailable")


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
