"""Read-only adapter for the legacy ChatBotProductMS Mongo collections."""
from __future__ import annotations

import os
import re
import time
import logging
import json
from functools import lru_cache
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pymongo import MongoClient, DESCENDING

from .chat_inputs import normalize_chat_input


_clients: dict[str, MongoClient] = {}
_shop_alias_cache: dict[tuple[str, str, str], tuple[float, bool]] = {}
_legacy_shop_name_cache: dict[tuple[str, str], tuple[float, str | None]] = {}
logger = logging.getLogger("blackshark.legacy_data")


def _client(kind: str) -> MongoClient:
    if kind not in _clients:
        if kind == "admin":
            uri = os.getenv("LEGACY_ADMIN_MONGO_URI") or os.getenv("ADMIN_MONGO_URI", "")
        else:
            uri = (os.getenv("LEGACY_COMMERCE_MONGO_URI") or os.getenv("ORDER_URI_MONGO")
                   or os.getenv("MONGO_URI", ""))
        if not uri:
            raise RuntimeError("legacy_mongo_not_configured")
        _clients[kind] = MongoClient(uri, serverSelectionTimeoutMS=5000,
                                     connectTimeoutMS=5000, socketTimeoutMS=10000)
    return _clients[kind]


def _admin_db():
    return _client("admin")[os.getenv("LEGACY_ADMIN_MONGO_DB") or os.getenv("ADMIN_MONGO_DB", "chatbot")]


def _commerce_db():
    return _client("commerce")[os.getenv("LEGACY_COMMERCE_MONGO_DB") or os.getenv("ORDER_DB")
                                or os.getenv("MONGO_DB", "dbWallet")]


def _collection(db: Any, setting: str, fallback: str):
    return db[os.getenv(setting, fallback).strip() or fallback]


def _scope_by_key(scopes: list[dict[str, Any]], platform: str, shop_id: str) -> dict[str, Any] | None:
    platform_key, shop_key = platform.strip().casefold(), shop_id.strip()
    if not platform_key or not shop_key:
        return None
    return next((scope for scope in scopes if str(scope.get("platform") or "").casefold() == platform_key
                 and str(scope.get("shop_id") or "") == shop_key), None)


def shop_alias_is_unique(platform: str, shop_id: str, legacy_shop_name: str) -> bool:
    """Commerce collections have shopname only, so refuse ambiguous name mappings."""
    if not legacy_shop_name.strip():
        return False
    key = (platform.casefold(), shop_id, legacy_shop_name.casefold())
    cached = _shop_alias_cache.get(key)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    result = (resolve_legacy_shop_name(platform, shop_id) or "").casefold() == legacy_shop_name.strip().casefold()
    _shop_alias_cache[key] = (time.monotonic(), result)
    return result


def resolve_legacy_shop_name(platform: str, shop_id: str) -> str | None:
    """Resolve Mongo's shopname alias from the scoped legacy conversation ID."""
    platform_key, shop_key = platform.strip().casefold(), shop_id.strip()
    if not platform_key or not shop_key:
        return None
    cache_key = (platform_key, shop_key)
    cached = _legacy_shop_name_cache.get(cache_key)
    if cached and time.monotonic() - cached[0] < 60:
        return cached[1]
    conversations = _conversation_collection()
    aliases = [str(value).strip() for value in conversations.distinct(
        "shop_name", {"platform": platform_key, "shop_id": {"$in": _shop_id_values(shop_key)}})
        if isinstance(value, str) and value.strip()]
    unique_aliases = {value.casefold(): value for value in aliases}
    resolved = next(iter(unique_aliases.values())) if len(unique_aliases) == 1 else None
    if resolved:
        owners = conversations.distinct("shop_id", {
            "platform": platform_key,
            "shop_name": re.compile("^" + re.escape(resolved) + "$", re.I),
        })
        if {str(value) for value in owners} != {shop_key}:
            resolved = None
    _legacy_shop_name_cache[cache_key] = (time.monotonic(), resolved)
    return resolved


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _iso(value: Any) -> str | None:
    parsed = _dt(value)
    return parsed.isoformat() if parsed else None


def _message_collection():
    return _collection(_admin_db(), "LEGACY_MESSAGES_COLLECTION", "messages_shp")


def _conversation_collection():
    return _collection(_admin_db(), "LEGACY_CONVERSATIONS_COLLECTION", "conversations_shp")


def _shadow_collection():
    return _collection(_admin_db(), "LEGACY_SHADOW_REPLIES_COLLECTION", "shadow_replies")


def _orders_collection():
    return _collection(_commerce_db(), "LEGACY_ORDERS_COLLECTION",
                       os.getenv("ORDER_COLLECTION", "ShpOrders"))


def _products_collection():
    return _collection(_commerce_db(), "LEGACY_PRODUCTS_COLLECTION",
                       os.getenv("MONGO_COLLECTION", "ShpProducts"))


def _item_id_values(values: list[str]) -> list[Any]:
    result: list[Any] = []
    for value in values:
        clean = str(value).strip()
        if not clean:
            continue
        result.append(clean)
        if clean.isdigit():
            numeric = int(clean)
            result.append(numeric)
            if numeric <= 2**53:
                result.append(float(numeric))
    return result


def _shop_id_values(shop_id: str) -> list[Any]:
    values: list[Any] = [shop_id]
    if shop_id.isdigit():
        values.append(int(shop_id))
    return values


def _trusted_media_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        allowed = host.endswith(".supabase.co") or host.endswith(".susercontent.com") or host in {
            "cf.shopee.co.th", "img.sp.mms.shopee.sg", "deo.shopeemobile.com",
        }
        return parsed.scheme == "https" and allowed and not parsed.username and not parsed.password and parsed.port in (None, 443)
    except ValueError:
        return False


@lru_cache(maxsize=128)
def _fetch_shopee_sticker_json(url: str) -> dict[str, Any]:
    """Fetch Shopee's public sticker metadata with a bounded response and fixed host."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "deo.shopeemobile.com":
        raise ValueError("untrusted_sticker_metadata_host")
    request = Request(url, headers={"User-Agent": "BlackSharkSupport/1.0"})
    with urlopen(request, timeout=2) as response:
        body = response.read(1_000_001)
    if not body or len(body) > 1_000_000:
        raise ValueError("sticker_metadata_size_invalid")
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError("sticker_metadata_invalid")
    return result


def _sticker_extension_from_pack(payload: Any, sticker_id: str) -> str | None:
    target = str(sticker_id).strip()

    def same_id(value: Any) -> bool:
        candidate = str(value).strip()
        if candidate == target:
            return True
        return candidate.isdigit() and target.isdigit() and int(candidate) == int(target)

    def extension(value: Any) -> str | None:
        if not isinstance(value, dict):
            return None
        for key in ("ext", "extension"):
            candidate = str(value.get(key) or "").strip().lower().lstrip(".")
            if candidate in {"png", "jpg", "jpeg", "webp", "gif"}:
                return candidate
        return None

    def visit(value: Any, depth: int = 0) -> str | None:
        if depth > 12:
            return None
        if isinstance(value, dict):
            ids = [value.get(key) for key in ("sid", "sticker_id", "stickerId", "id")]
            if any(identifier is not None and same_id(identifier) for identifier in ids):
                found = extension(value)
                if found:
                    return found
            # Some pack versions key the sticker records by sid instead of storing it in each record.
            keyed = value.get(target)
            found = extension(keyed)
            if found:
                return found
            for child in value.values():
                found = visit(child, depth + 1)
                if found:
                    return found
        elif isinstance(value, list):
            for child in value[:500]:
                found = visit(child, depth + 1)
                if found:
                    return found
        return None

    return visit(payload)


def _resolve_shopee_sticker_extension(package_id: str, sticker_id: str, region: str) -> str | None:
    package = str(package_id or "").strip()
    sticker = str(sticker_id or "").strip()
    region_key = str(region or "").strip().lower()
    if (not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", package)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", sticker)
            or not re.fullmatch(r"[a-z]{2}", region_key)):
        return None
    base = f"https://deo.shopeemobile.com/shopee/shopee-sticker-live-{region_key}"
    try:
        manifest = _fetch_shopee_sticker_json(f"{base}/manifest.json")
        packs = manifest.get("packs")
        if not isinstance(packs, list) or not any(
                isinstance(pack, dict)
                and str(pack.get("pid") or "") == package
                and isinstance(pack.get("reg"), list)
                and region_key.upper() in {str(item).upper() for item in pack["reg"]}
                for pack in packs):
            return None
        pack_data = _fetch_shopee_sticker_json(f"{base}/packs/{package}/{package}.json")
        return _sticker_extension_from_pack(pack_data, sticker)
    except Exception as exc:
        logger.debug("shopee_sticker_metadata_unavailable type=%s", type(exc).__name__)
        return None


def _message_event(raw_payload: Any, fallback_text: str) -> tuple[Any, dict[str, Any]]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    outer = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    inner = outer.get("content") if isinstance(outer.get("content"), dict) else payload
    content = inner.get("content") if isinstance(inner.get("content"), dict) else {}
    normalized = normalize_chat_input(payload, fallback_text)
    if normalized.message_type == "bundle_message":
        sub_ids = normalized.metadata.get("bundle_message_ids")
        if not isinstance(sub_ids, list):
            sub_ids = [str(value) for value in content.get("messages", [])[:20]
                       if isinstance(value, (str, int)) and not isinstance(value, bool)] \
                if isinstance(content.get("messages"), list) else []
        normalized = replace(normalized, metadata={**normalized.metadata, "bundle_message_ids": sub_ids})
    media: list[dict[str, str]] = []
    seen: set[str] = set()
    thumbnail_urls: set[str] = set()
    sticker_thumbnail_media: list[dict[str, str]] = []
    image_keys = {"url", "image_url", "thumb_url", "thumbnail_url", "image", "pic", "file_url",
                  "sticker_url", "sticker_image_url", "sticker_thumb_url",
                  "video_url", "video_download_url", "download_url", "original_url"}
    thumbnail_keys = {"thumb_url", "thumbnail_url", "sticker_thumb_url"}

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                lowered = str(key).lower()
                if lowered in image_keys and isinstance(child, str):
                    url = child.strip()
                    if url and not url.startswith(("http://", "https://")) and lowered in image_keys:
                        url = "https://img.sp.mms.shopee.sg/" + url.lstrip("/")
                    if _trusted_media_url(url) and url not in seen:
                        kind = "video" if "video" in lowered or (msg_type == "video" and lowered == "url") else "image"
                        media.append({"url": url[:2048], "kind": kind})
                        seen.add(url)
                        if lowered in thumbnail_keys:
                            thumbnail_urls.add(url)
                elif isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:30]:
                walk(child, depth + 1)

    msg_type = normalized.message_type
    if msg_type in {"image", "image_with_text", "video", "sticker"}:
        walk(inner)
    if msg_type in {"image", "image_with_text", "sticker"}:
        full_size_media = [item for item in media if item["url"] not in thumbnail_urls]
        if full_size_media:
            media = full_size_media
        elif msg_type == "sticker":
            sticker_thumbnail_media = media
            media = []
    if msg_type == "sticker" and not media:
        package = str(normalized.metadata.get("sticker_package_id") or "")
        sticker = str(normalized.metadata.get("sticker_id") or "")
        region = str(normalized.metadata.get("sticker_region") or os.getenv("SHOPEE_STICKER_REGION", "th"))
        extension = str(normalized.metadata.get("sticker_extension") or "").lower().lstrip(".")
        if extension not in {"png", "jpg", "jpeg", "webp", "gif"}:
            extension = _resolve_shopee_sticker_extension(package, sticker, region) or "png"
        if (re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", package)
                and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", sticker)
                and re.fullmatch(r"[a-zA-Z]{2}", region)
                and extension in {"png", "jpg", "jpeg", "webp", "gif"}):
            url = (f"https://deo.shopeemobile.com/shopee/shopee-sticker-live-{region.lower()}"
                   f"/packs/{package}/{sticker}@1x.{extension}")
            media.append({"url": url,
                          "kind": "image"})
        if not media:
            media = sticker_thumbnail_media
    return normalized, {"media": media[:10]}


def _expand_bundle(event: Any, media: list[dict[str, str]], conversation_id: str,
                   shop_id: str, platform: str) -> tuple[Any, list[dict[str, str]]]:
    """Resolve only submessages referenced by this bundle inside its scoped conversation."""
    ids = event.metadata.get("bundle_message_ids") or []
    if event.message_type != "bundle_message" or not ids:
        return event, media
    rows = _message_collection().find({"conversation_id": conversation_id,
        "shop_id": {"$in": _shop_id_values(shop_id)}, "platform": platform,
        "message_id": {"$in": ids}, "$or": [{"role": "user"}, {"direction": "in"}]},
        {"_id": 0, "message_id": 1, "raw_payload": 1, "text": 1}).limit(20)
    by_id = {str(row.get("message_id") or ""): row for row in rows}
    children = [_message_event(by_id[mid].get("raw_payload"), str(by_id[mid].get("text") or ""))
                for mid in ids if mid in by_id]
    item_ids = list(dict.fromkeys([*event.item_ids, *(item_id for child, _ in children for item_id in child.item_ids)]))
    text = event.text or " ".join(child.text for child, _ in children if child.text)[:1000]
    all_media = [*media, *(image for _, payload in children for image in payload["media"])]
    deduped = list({image["url"]: image for image in all_media}.values())[:10]
    return replace(event, text=text, item_ids=tuple(item_ids[:10]),
                   children=tuple(child for child, _ in children)), deduped


def product_identities_by_ids(item_ids: list[str], shop_name: str) -> dict[str, dict[str, str]]:
    """Resolve card IDs to listing names only; product facts remain in the shop KB."""
    ids = list(dict.fromkeys(str(value).strip() for value in item_ids if str(value).strip()))[:20]
    if not ids or not shop_name.strip():
        return {}
    query = {"item_id": {"$in": _item_id_values(ids)}, "shopname": shop_name.strip()}
    projection = {"_id": 0, "item_id": 1, "item_name": 1, "name": 1}
    rows = _products_collection().find(query, projection).limit(20)
    result: dict[str, dict[str, str]] = {}
    for doc in rows:
        stored_id = doc.get("item_id")
        if stored_id is None:
            continue
        canonical = str(int(stored_id)) if isinstance(stored_id, float) and stored_id.is_integer() else str(stored_id)
        result[canonical] = {
            "item_id": canonical,
            "name": str(doc.get("item_name") or doc.get("name") or "")[:300],
        }
    return result


def list_conversations(*, allowed_shops: list[dict[str, Any]], query: str = "", shop_id: str = "",
                       platform: str = "", page: int = 1, limit: int = 60) -> dict[str, Any]:
    requested_scope = _scope_by_key(allowed_shops, platform, shop_id) if shop_id and platform else None
    scopes = [requested_scope] if requested_scope else [scope for scope in allowed_shops
        if (not shop_id or str(scope.get("shop_id")) == shop_id)
        and (not platform or str(scope.get("platform") or "").casefold() == platform.casefold())]
    if not scopes:
        return {"conversations": [], "page": max(page, 1), "limit": min(max(limit, 1), 100)}
    scope_filters = [{"platform": scope["platform"], "shop_id": {"$in": _shop_id_values(str(scope["shop_id"]))}}
                     for scope in scopes]
    collection = _conversation_collection()
    filters: dict[str, Any] = {"$or": scope_filters}
    if query.strip():
        term = re.escape(query.strip()[:100])
        filters = {"$and": [filters, {"$or": [{field: re.compile(term, re.I)} for field in
                           ("to_name", "customer_id", "last_message_text", "conversation_id")]}]}
    projection = {"_id": 0, "conversation_id": 1, "customer_id": 1, "shop_id": 1,
                  "shop_name": 1, "platform": 1, "to_name": 1, "last_message_id": 1,
                  "last_message_text": 1, "last_message_timestamp": 1, "unread_count": 1,
                  "status": 1, "updated_at": 1}
    safe_limit = min(max(limit, 1), 100)
    rows = list(collection.find(filters, projection)
                 .sort([("last_message_timestamp", DESCENDING), ("conversation_id", DESCENDING)])
                 .skip((max(page, 1) - 1) * safe_limit).limit(safe_limit))
    scope_names = {(scope["platform"], scope["shop_id"]): scope["shop_name"] for scope in scopes}
    safe_rows = []
    for row in rows:
        key = (str(row.get("platform") or "").casefold(), str(row.get("shop_id") or ""))
        if key not in scope_names:
            key = (key[0], str(int(key[1])) if key[1].isdigit() else key[1])
        canonical_name = scope_names.get(key)
        if not canonical_name:
            continue
        safe_rows.append({**row, "legacy_shop_name": str(row.get("shop_name") or ""),
                          "shop_name": canonical_name,
                          "last_message_timestamp": _iso(row.get("last_message_timestamp")),
                          "updated_at": _iso(row.get("updated_at"))})
    return {"conversations": safe_rows,
            "page": max(page, 1), "limit": safe_limit}


def get_conversation(conversation_id: str, shop_id: str, platform: str,
                     allowed_shops: list[dict[str, Any]]) -> dict[str, Any] | None:
    scope = _scope_by_key(allowed_shops, platform, shop_id)
    if not scope:
        return None
    row = _conversation_collection().find_one({"conversation_id": conversation_id,
        "shop_id": {"$in": _shop_id_values(shop_id)}, "platform": scope["platform"]}, {"_id": 0})
    if not row:
        return None
    row["legacy_shop_name"] = str(row.get("shop_name") or "")
    row["shop_name"] = scope["shop_name"]
    row["last_message_timestamp"] = _iso(row.get("last_message_timestamp"))
    row["updated_at"] = _iso(row.get("updated_at"))
    row["created_at"] = _iso(row.get("created_at"))
    return row


def _shadow_rows(conversation_id: str, shop_id: str, platform: str, inbound_ids: list[str] | None = None,
                 limit: int = 300) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"conversation_id": conversation_id, "shop_id": {"$in": _shop_id_values(shop_id)}, "platform": platform,
                             "bot_reply_text": {"$exists": True, "$nin": ["", None]},
                             "$or": [{"deleted_at": {"$exists": False}}, {"deleted_at": None}]}
    if inbound_ids is not None:
        query["inbound_message_id"] = {"$in": inbound_ids} if inbound_ids else {"$in": []}
    projection = {"_id": 0, "shadow_reply_id": 1, "conversation_id": 1, "shop_id": 1, "platform": 1,
                  "inbound_message_id": 1, "bot_reply_text": 1, "bot_image_desc": 1, "bot_products": 1,
                  "bot_model": 1, "bot_source": 1, "bot_handoff_to_admin": 1,
                  "bot_handoff_reason": 1, "origin": 1, "mode": 1, "created_at": 1}
    return list(_shadow_collection().find(query, projection).sort("created_at", DESCENDING).limit(limit))


def _message_rows(conversation_id: str, shop_id: str, platform: str, *, before: datetime | None = None,
                  before_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"conversation_id": conversation_id, "shop_id": {"$in": _shop_id_values(shop_id)}, "platform": platform}
    if before:
        query["$or"] = ([{"created_timestamp": {"$lt": before}},
                         {"created_timestamp": before, "message_id": {"$lt": before_id}}]
                        if before_id else [{"created_timestamp": {"$lt": before}}])
    projection = {"_id": 0, "message_id": 1, "conversation_id": 1, "shop_id": 1, "platform": 1,
                  "role": 1, "direction": 1, "source": 1, "actor": 1, "text": 1,
                  "raw_payload": 1, "created_timestamp": 1}
    rows = list(_message_collection().find(query, projection).sort(
        [("created_timestamp", DESCENDING), ("message_id", DESCENDING)]).limit(min(max(limit, 1), 500)))
    return list(reversed(rows))


def _format_message(row: dict[str, Any], shop_name: str, products: dict[str, dict[str, Any]],
                    shadows_by_inbound: dict[str, list[dict[str, Any]]],
                    conversation_id: str, shop_id: str, platform: str) -> dict[str, Any]:
    message_id = str(row.get("message_id") or "")
    role = str(row.get("role") or "")
    direction = str(row.get("direction") or "")
    is_user = role == "user" or direction == "in"
    event, media_payload = _message_event(row.get("raw_payload"), str(row.get("text") or ""))
    event, media_payload["media"] = _expand_bundle(event, media_payload["media"], conversation_id, shop_id, platform)
    item_ids = list(event.item_ids)
    cards = [products[item_id] for item_id in item_ids if item_id in products]
    shadow_rows = shadows_by_inbound.get(message_id, [])
    image_descriptions = list(dict.fromkeys(
        str(item.get("bot_image_desc") or "").strip() for item in shadow_rows if item.get("bot_image_desc")))
    if is_user:
        content = str(event.text or event.metadata.get("notification_text") or row.get("text") or "").strip()
        message_role = "user"
        label = "ลูกค้า"
    else:
        content = str(row.get("text") or "").strip()
        message_role = "assistant"
        label = str(row.get("source") or role or "ร้านค้า")
    return {"id": message_id, "message_id": message_id, "role": message_role, "label": label,
            "content": content, "created_at": _iso(row.get("created_timestamp")),
            "platform": row.get("platform"), "direction": direction,
            "message_type": event.message_type, "item_ids": item_ids,
            "order_sn": event.order_sn, "media": media_payload["media"],
            "vision_descriptions": image_descriptions, "product_cards": cards,
            "shop_name": shop_name}


def list_messages(conversation_id: str, shop_id: str, platform: str,
                  allowed_shops: list[dict[str, Any]], *,
                  before_at: str | None = None, before_id: str | None = None,
                  limit: int = 100) -> dict[str, Any]:
    conversation = get_conversation(conversation_id, shop_id, platform, allowed_shops)
    if not conversation:
        return {"messages": [], "has_more": False, "conversation": None}
    before = _dt(before_at) if before_at else None
    # Read an extra row for pagination; all data remains scoped to this exact conversation/shop.
    rows = _message_rows(conversation_id, shop_id, platform, before=before,
                         before_id=before_id, limit=min(limit, 100) + 1)
    has_more = len(rows) > min(limit, 100)
    if has_more:
        rows = rows[-min(limit, 100):]
    item_ids = []
    for row in rows:
        event, media = _message_event(row.get("raw_payload"), str(row.get("text") or ""))
        event, _ = _expand_bundle(event, media["media"], conversation_id, shop_id, platform)
        item_ids.extend(event.item_ids)
    legacy_name = str(conversation.get("legacy_shop_name") or "")
    products = {}
    if platform.casefold() == "shopee" and item_ids:
        try:
            if shop_alias_is_unique(platform, shop_id, legacy_name):
                products = product_identities_by_ids(item_ids, legacy_name)
        except Exception as exc:
            logger.warning("ticket_card_lookup_failed type=%s", type(exc).__name__)
    shadows = _shadow_rows(conversation_id, shop_id, platform,
                           [str(row.get("message_id") or "") for row in rows if row.get("role") == "user"],
                           limit=500)
    by_inbound: dict[str, list[dict[str, Any]]] = {}
    for shadow in shadows:
        by_inbound.setdefault(str(shadow.get("inbound_message_id") or ""), []).append(shadow)
    messages = [_format_message(row, str(conversation.get("shop_name") or ""), products, by_inbound,
                               conversation_id, shop_id, platform) for row in rows]
    existing_answers = {" ".join(str(message.get("content") or "").split()).casefold()
                        for message in messages if message.get("role") == "assistant"}
    for shadow in shadows:
        text = str(shadow.get("bot_reply_text") or "").strip()
        if not text or " ".join(text.split()).casefold() in existing_answers:
            continue
        messages.append({"id": str(shadow.get("shadow_reply_id") or f"shadow:{shadow.get('inbound_message_id')}"),
            "message_id": str(shadow.get("inbound_message_id") or ""), "role": "assistant",
            "label": f"{conversation.get('shop_name') or 'ร้านค้า'} bot", "content": text, "created_at": _iso(shadow.get("created_at")),
            "platform": platform, "source": "shadow_reply", "origin": shadow.get("origin"),
            "model": shadow.get("bot_model"), "image_description": shadow.get("bot_image_desc"),
            "handoff_required": bool(shadow.get("bot_handoff_to_admin")),
            "handoff_reason": shadow.get("bot_handoff_reason"), "product_cards": []})
    messages.sort(key=lambda row: (row.get("created_at") or "", row.get("role") == "assistant", row.get("id") or ""))
    return {"conversation": conversation, "messages": messages, "has_more": has_more,
            "next_before_at": _iso(rows[0].get("created_timestamp")) if rows else None,
            "next_before_id": str(rows[0].get("message_id") or "") if rows else None}


def get_inbound_message(conversation_id: str, message_id: str, shop_id: str,
                        platform: str, allowed_shops: list[dict[str, Any]]) -> dict[str, Any] | None:
    conversation = get_conversation(conversation_id, shop_id, platform, allowed_shops)
    if not conversation:
        return None
    row = _message_collection().find_one({"conversation_id": conversation_id, "message_id": message_id,
        "shop_id": {"$in": _shop_id_values(shop_id)}, "platform": platform,
        "$or": [{"role": "user"}, {"direction": "in"}]},
        {"_id": 0, "message_id": 1, "text": 1, "raw_payload": 1, "created_timestamp": 1})
    if not row:
        return None
    event, media_payload = _message_event(row.get("raw_payload"), str(row.get("text") or ""))
    event, media_payload["media"] = _expand_bundle(event, media_payload["media"], conversation_id, shop_id, platform)
    return {**row, "created_timestamp": _dt(row.get("created_timestamp")),
            "event": event, "media": media_payload["media"],
            "shop_name": conversation.get("shop_name"), "legacy_shop_name": conversation.get("legacy_shop_name"),
            "customer_id": conversation.get("customer_id")}


def history_for_conversation(conversation_id: str, shop_id: str, platform: str,
                             shop_name: str, before: datetime | None = None,
                             *, max_pairs: int = 5) -> list[dict[str, Any]]:
    rows = _message_rows(conversation_id, shop_id, platform, before=before, limit=250)
    user_rows = [row for row in rows if row.get("role") == "user" or row.get("direction") == "in"]
    ids = [str(row.get("message_id") or "") for row in user_rows]
    products_by_id = {}
    if platform.casefold() == "shopee":
        try:
            item_ids = []
            for row in user_rows:
                event, media = _message_event(row.get("raw_payload"), str(row.get("text") or ""))
                event, _ = _expand_bundle(event, media["media"], conversation_id, shop_id, platform)
                item_ids.extend(event.item_ids)
            products_by_id = product_identities_by_ids(item_ids, shop_name) if shop_alias_is_unique(platform, shop_id, shop_name) else {}
        except Exception as exc:
            logger.warning("ticket_history_card_lookup_failed type=%s", type(exc).__name__)
    shadows = _shadow_rows(conversation_id, shop_id, platform, ids, limit=500)
    shadow_by_id: dict[str, dict[str, Any]] = {}
    for shadow in shadows:
        inbound_id = str(shadow.get("inbound_message_id") or "")
        # Prefer the newest reply generated by this engine; otherwise use the newest shadow answer.
        old = shadow_by_id.get(inbound_id)
        score = (shadow.get("origin") in {"chatblackshark", "chatblackshark_ticket", "worker", "workflow"},
                 _dt(shadow.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
        old_score = ((old or {}).get("origin") in {"chatblackshark", "chatblackshark_ticket", "worker", "workflow"},
                     _dt((old or {}).get("created_at")) or datetime.min.replace(tzinfo=timezone.utc))
        if old is None or score > old_score:
            shadow_by_id[inbound_id] = shadow

    group_inputs: list[dict[str, Any]] = []
    for row in rows:
        role = str(row.get("role") or "")
        direction = str(row.get("direction") or "")
        if role == "user" or direction == "in":
            event, media = _message_event(row.get("raw_payload"), str(row.get("text") or ""))
            event, media["media"] = _expand_bundle(event, media["media"], conversation_id, shop_id, platform)
            item_ids = list(event.item_ids)
            cards = [products_by_id[item_id] for item_id in item_ids if item_id in products_by_id]
            user_content = str(event.text or event.metadata.get("notification_text") or row.get("text") or "").strip()
            if event.message_type == "order" and event.order_sn and not user_content:
                user_content = f"[การ์ดคำสั่งซื้อ เลข {event.order_sn}]"
            group_inputs.append({"role": "user", "content": user_content,
                                 "created_at": _dt(row.get("created_timestamp")), "message_id": str(row.get("message_id") or ""),
                                 "product_name": cards[0]["name"] if len(cards) == 1 else None,
                                 "product_names": [card["name"] for card in cards],
                                 "card_title": cards[0]["name"] if len(cards) == 1 else None,
                                 "card_product_names": [card["name"] for card in cards],
                                 "card_context": cards,
                                 "vision_descriptions": [str(shadow_by_id.get(str(row.get("message_id") or ""), {}).get("bot_image_desc") or "").strip()]
                                    if shadow_by_id.get(str(row.get("message_id") or ""), {}).get("bot_image_desc") else [],
                                 "media": media["media"]})
        elif direction == "out" and role in {"admin", "bot"} and not row.get("actor"):
            content = str(row.get("text") or "").strip()
            if content:
                group_inputs.append({"role": "assistant", "content": content,
                                     "created_at": _dt(row.get("created_timestamp"))})

    grouped: list[list[dict[str, Any]]] = []
    pending_users: list[dict[str, Any]] = []
    for event in group_inputs:
        if event["role"] == "user":
            pending_users.append(event)
        elif pending_users:
            # Prefer a BotWorker shadow result for the same inbound message, then use legacy outbound text.
            replies = [shadow_by_id.get(str(item.get("message_id") or "")) for item in pending_users]
            reply = next((item for item in replies if item and item.get("bot_reply_text")), None)
            if reply:
                answer = {"role": "assistant", "content": str(reply["bot_reply_text"]),
                          "product_names": [str(x.get("name")) for x in (reply.get("bot_products") or [])
                                            if isinstance(x, dict) and x.get("name")],
                          "intent_analysis": {}, "created_at": _dt(reply.get("created_at"))}
            else:
                answer = event
            grouped.append([*pending_users, answer])
            pending_users = []
    if pending_users:
        replies = [shadow_by_id.get(str(item.get("message_id") or "")) for item in pending_users]
        reply = next((item for item in replies if item and item.get("bot_reply_text")), None)
        tail = [{"role": "assistant", "content": str(reply["bot_reply_text"]),
                 "product_names": [], "intent_analysis": {}}] if reply else []
        grouped.append([*pending_users, *tail])

    result: list[dict[str, Any]] = []
    for group in grouped[-max_pairs:]:
        user_text = " ".join(str(item.get("content") or "").strip() for item in group if item["role"] == "user").strip()
        user_rows = [item for item in group if item["role"] == "user"]
        product_names = list(dict.fromkeys(name for item in user_rows for name in item.get("product_names", [])))
        user_turn: dict[str, Any] = {"role": "user", "content": user_text,
            "product_name": product_names[0] if len(product_names) == 1 else None,
            "product_names": product_names[:10],
            "card_product_names": product_names[:10],
            "vision_descriptions": [text for item in user_rows for text in item.get("vision_descriptions", []) if text]}
        if len(user_rows) == 1 and user_rows[0].get("card_title"):
            user_turn["card_title"] = user_rows[0]["card_title"]
        result.append(user_turn)
        assistant = next((item for item in reversed(group) if item["role"] == "assistant"), None)
        if assistant and assistant.get("content"):
            result.append({"role": "assistant", "content": str(assistant["content"]),
                           "product_names": assistant.get("product_names", [])})
    return result


def _customer_ids(customer_id: str) -> list[Any]:
    values: list[Any] = [customer_id]
    if customer_id.isdigit():
        values.append(int(customer_id))
    return values


def _status_label(status: Any) -> str:
    value = str(status or "").upper()
    return {"UNPAID": "รอชำระเงิน", "READY_TO_SHIP": "รอจัดส่ง", "PROCESSED": "กำลังเตรียมสินค้า",
            "SHIPPED": "จัดส่งแล้ว", "TO_CONFIRM_RECEIVE": "รอรับสินค้า", "COMPLETED": "จัดส่งสำเร็จ",
            "IN_CANCEL": "กำลังดำเนินการยกเลิก", "CANCELLED": "ยกเลิกแล้ว", "TO_RETURN": "อยู่ระหว่างคืนสินค้า"}.get(value, value or "ไม่ระบุ")


def _tracking_numbers(doc: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for package in doc.get("package_list") or []:
        if not isinstance(package, dict):
            continue
        for key in ("tracking_no", "tracking_number", "parcel_id", "waybill_id"):
            value = str(package.get(key) or "").strip()
            if value and value not in found:
                found.append(value)
    return found


def _order_summary(doc: dict[str, Any]) -> dict[str, Any]:
    logistics = ""
    carrier = str(doc.get("shipping_carrier") or doc.get("checkout_shipping_carrier") or "")
    for package in doc.get("package_list") or []:
        if isinstance(package, dict):
            logistics = str(package.get("logistics_status") or logistics)
            carrier = carrier or str(package.get("shipping_carrier") or "")
    items = []
    for item in doc.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        items.append({"name": str(item.get("item_name") or ""), "model_name": str(item.get("model_name") or ""),
                      "quantity": int(item.get("model_quantity_purchased") or 1),
                      "item_id": str(item.get("item_id") or "").removesuffix(".0"),
                      "model_id": str(item.get("model_id") or "").removesuffix(".0")})
    return {"order_sn": str(doc.get("order_sn") or ""), "order_status": _status_label(doc.get("order_status")),
            "order_status_raw": str(doc.get("order_status") or ""), "logistics_status": _status_label(logistics),
            "logistics_status_raw": logistics, "shipping_carrier": carrier,
            "tracking_numbers": _tracking_numbers(doc), "items": items,
            "create_time": _iso(doc.get("create_time")), "update_time": _iso(doc.get("update_time") or doc.get("update_time_unix"))}


def list_customer_orders(customer_id: str, shop_name: str, limit: int = 10) -> list[dict[str, Any]]:
    if not customer_id or not shop_name:
        return []
    query = {"buyer_user_id": {"$in": _customer_ids(customer_id)}, "shopname": shop_name.strip()}
    projection = {"_id": 0, "order_sn": 1, "buyer_user_id": 1, "shopname": 1, "order_status": 1,
        "package_list": 1, "shipping_carrier": 1, "checkout_shipping_carrier": 1, "item_list": 1,
        "create_time": 1, "update_time": 1, "update_time_unix": 1}
    docs = (_orders_collection().find(query, projection).sort("create_time", DESCENDING)
            .limit(min(max(limit, 1), 20)).max_time_ms(5000))
    return [_order_summary(doc) for doc in docs]


def find_customer_order(customer_id: str, shop_name: str, order_sn: str = "") -> dict[str, Any] | None:
    if not customer_id or not shop_name:
        return None
    query: dict[str, Any] = {"buyer_user_id": {"$in": _customer_ids(customer_id)},
                             "shopname": shop_name.strip()}
    projection = {"_id": 0, "order_sn": 1, "order_status": 1, "package_list": 1,
                  "shipping_carrier": 1, "checkout_shipping_carrier": 1, "item_list": {"$slice": 10},
                  "create_time": 1, "update_time": 1, "update_time_unix": 1}
    if order_sn:
        query["order_sn"] = order_sn
        doc = _orders_collection().find_one(query, projection, max_time_ms=5000)
        return _order_summary(doc) if doc else None
    docs = list(_orders_collection().find(query, projection).sort("create_time", DESCENDING)
                .limit(1).max_time_ms(5000))
    return _order_summary(docs[0]) if docs else None


def get_customer_summary(customer_id: str, platform: str = "shopee") -> dict[str, Any] | None:
    if not customer_id:
        return None
    name = os.getenv("LEGACY_CUSTOMERS_COLLECTION", os.getenv("ADMIN_MONGO_COLLECTION_CUSTOMERS", "customers_shp"))
    return _collection(_admin_db(), "LEGACY_CUSTOMERS_COLLECTION", name).find_one(
        {"customer_id": customer_id, "platform": platform},
        {"_id": 0, "customer_id": 1, "platform": 1, "conversations": 1,
         "shops": 1, "last_active_at": 1, "created_at": 1})
