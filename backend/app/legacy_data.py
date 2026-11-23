"""Read-only adapter for the legacy ChatBotProductMS Mongo collections."""
from __future__ import annotations

import os
import re
import time
import logging
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

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


def _model_summary(model: dict[str, Any]) -> dict[str, Any]:
    stock_info = model.get("stock_info_v2") or {}
    summary = stock_info.get("summary_info") or {}
    stock = summary.get("total_available_stock")
    stock_known = isinstance(stock, (int, float))
    if not stock_known:
        for key in ("shopee_stock", "seller_stock"):
            values = stock_info.get(key)
            if isinstance(values, list):
                usable = [item for item in values if isinstance(item, dict)
                          and isinstance(item.get("stock"), (int, float))
                          and (key != "seller_stock" or item.get("if_saleable") is not False)]
                if usable:
                    stock, stock_known = sum(int(item["stock"]) for item in usable), True
                    break
    available = int(stock or 0) if stock_known else None
    return {"model_id": str(model.get("model_id") or "").removesuffix(".0"),
            "model_name": str(model.get("model_name") or "")[:300],
            "model_status": str(model.get("model_status") or ""),
            "stock_known": stock_known,
            "available": available if stock_known else None}


def _tier_variation_summary(value: Any) -> list[dict[str, Any]]:
    """Keep Shopee's selectable options as structured evidence, without raw listing data."""
    entries = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    result = []
    for entry in entries[:10]:
        if not isinstance(entry, dict):
            continue
        options = entry.get("options")
        if not isinstance(options, list):
            options = entry.get("option_list")
        if not isinstance(options, list):
            continue
        normalized_options = [option.get("option") if isinstance(option, dict) else option for option in options]
        clean_options = list(dict.fromkeys(str(option).strip()[:120] for option in normalized_options
                                           if isinstance(option, (str, int, float)) and str(option).strip()))[:50]
        if clean_options:
            result.append({"name": str(entry.get("name") or "ตัวเลือก")[:120], "options": clean_options})
    return result


def _item_availability(item_status: str, models: list[dict[str, Any]]) -> str:
    active = [model for model in models if model["model_status"].upper() == "MODEL_NORMAL"]
    known = [model for model in active if model["stock_known"]]
    availability = "available" if any((model["available"] or 0) > 0 for model in known) else (
        "unavailable" if known or not active else "unknown")
    if item_status.upper() != "NORMAL" and availability == "available":
        return "unavailable"
    return availability


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


def _message_event(raw_payload: Any, fallback_text: str) -> tuple[Any, dict[str, Any]]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    outer = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    inner = outer.get("content") if isinstance(outer.get("content"), dict) else payload
    content = inner.get("content") if isinstance(inner.get("content"), dict) else {}
    source = (inner.get("source_content") if isinstance(inner.get("source_content"), dict)
              else outer.get("source_content") if isinstance(outer.get("source_content"), dict)
              else payload.get("source_content") if isinstance(payload.get("source_content"), dict) else {})
    normalized = normalize_chat_input({
        "message_type": inner.get("message_type") or payload.get("message_type") or payload.get("msg_type"),
        "text": content.get("text") or fallback_text,
        "content": {**content, "source_content": source,
                    "item_id": content.get("item_id") or content.get("product_id")},
        "item_id": inner.get("item_id"), "item_ids": inner.get("item_ids"),
        "order_sn": inner.get("order_sn"),
    }, fallback_text)
    if normalized.message_type == "bundle_message":
        sub_ids = [str(value) for value in content.get("messages", [])[:20]
                   if isinstance(value, (str, int))] if isinstance(content.get("messages"), list) else []
        normalized = replace(normalized, metadata={**normalized.metadata, "bundle_message_ids": sub_ids})
    media: list[dict[str, str]] = []
    seen: set[str] = set()
    image_keys = {"url", "image_url", "thumb_url", "thumbnail_url", "image", "pic", "file_url",
                  "sticker_url", "sticker_image_url", "sticker_thumb_url",
                  "video_url", "video_download_url", "download_url", "original_url"}

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
                        media.append({"url": url[:2048], "kind": "video" if "video" in lowered or msg_type == "video" and lowered == "url" else "image"})
                        seen.add(url)
                elif isinstance(child, (dict, list)):
                    walk(child, depth + 1)
        elif isinstance(value, list):
            for child in value[:30]:
                walk(child, depth + 1)

    msg_type = normalized.message_type
    if msg_type in {"image", "image_with_text", "video", "sticker", "bundle_message"}:
        walk(inner)
    if msg_type == "sticker" and not media:
        package = content.get("sticker_package_id")
        sticker = content.get("sticker_id")
        if isinstance(package, str) and isinstance(sticker, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", package) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", sticker):
            media.append({"url": f"https://deo.shopeemobile.com/shopee/shopee-sticker-live-th/packs/{package}/{sticker}@1x.png", "kind": "image"})
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


def _products_by_ids(item_ids: list[str], shop_name: str) -> dict[str, dict[str, Any]]:
    ids = list(dict.fromkeys(item_ids))[:20]
    if not ids:
        return {}
    collection = _products_collection()
    query: dict[str, Any] = {"item_id": {"$in": _item_id_values(ids)}}
    if not shop_name.strip():
        return {}
    query["shopname"] = shop_name.strip()
    projection = {"_id": 0, "item_id": 1, "item_name": 1, "name": 1, "description": 1,
                 "shopname": 1, "brand": 1, "image": 1, "images": 1, "image_url": 1,
                 "item_status": 1, "model": 1, "tier_variation": 1}
    rows = collection.find(query, projection).limit(20)
    result: dict[str, dict[str, Any]] = {}
    for doc in rows:
        stored_id = doc.get("item_id")
        canonical = str(int(stored_id)) if isinstance(stored_id, float) and stored_id.is_integer() else str(stored_id)
        image = doc.get("image_url")
        images = doc.get("images")
        if isinstance(images, dict):
            images = images.get("image_url_list") or images.get("image_id_list") or []
        if not image and isinstance(images, list) and images:
            image = images[0]
        if not image:
            item_image = doc.get("image") or {}
            image_ids = item_image.get("image_url_list") or item_image.get("image_id_list") or [] if isinstance(item_image, dict) else []
            image = image_ids[0] if image_ids else ""
        if image and not str(image).startswith("http"):
            image = "https://cf.shopee.co.th/file/" + str(image).lstrip("/")
        if image and not _trusted_media_url(str(image)):
            image = ""
        models = [_model_summary(model) for model in doc.get("model", []) if isinstance(model, dict)][:100]
        item_status = str(doc.get("item_status") or "")
        result[canonical] = {
            "item_id": canonical,
            "name": str(doc.get("item_name") or doc.get("name") or "")[:300],
            "description": str(doc.get("description") or "")[:8000],
            "image_url": str(image or "")[:2048],
            "shopname": str(doc.get("shopname") or ""),
            "item_status": item_status, "availability": _item_availability(item_status, models),
            "models": models, "tier_variation": _tier_variation_summary(doc.get("tier_variation")),
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
        content = str(event.text or row.get("text") or "").strip()
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
                products = _products_by_ids(item_ids, legacy_name)
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
            products_by_id = _products_by_ids(item_ids, shop_name) if shop_alias_is_unique(platform, shop_id, shop_name) else {}
        except Exception as exc:
            logger.warning("ticket_history_card_lookup_failed type=%s", type(exc).__name__)
    shadows = _shadow_rows(conversation_id, shop_id, platform, ids, limit=500)
    shadow_by_id: dict[str, dict[str, Any]] = {}
    for shadow in shadows:
        inbound_id = str(shadow.get("inbound_message_id") or "")
        # Prefer the newest Black Shark generated reply; otherwise keep the newest available shadow answer.
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
            group_inputs.append({"role": "user", "content": str(event.text or row.get("text") or "").strip(),
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


def products_for_catalog_names(names: list[str], catalog: list[dict[str, Any]],
                               shop_name: str) -> list[dict[str, Any]]:
    """Match KB model labels to listings and units, rejecting ambiguous category matches."""
    if not names or not shop_name:
        return []
    labels = [str(item.get("name") or "") for item in catalog if item.get("name")]
    requested = set(names) & set(labels)
    if not requested:
        return []

    def words(value: str) -> tuple[str, ...]:
        return tuple(re.findall(r"[a-z0-9]+", value.casefold().replace("black shark", "")))

    aliases = {label: words(label) for label in labels}

    def matches(value: str) -> list[str]:
        tokens = words(value)
        found = []
        for label, alias in aliases.items():
            if alias and any(tokens[index:index + len(alias)] == alias
                             for index in range(len(tokens) - len(alias) + 1)):
                found.append(label)
        return found

    def best_matches(value: str) -> list[str]:
        found = matches(value)
        longest = max((len(aliases[label]) for label in found), default=0)
        return [label for label in found if len(aliases[label]) == longest]

    projection = {"_id": 0, "item_id": 1, "item_name": 1, "shopname": 1,
                  "item_status": 1, "description": 1, "image": 1, "images": 1,
                  "category_id": 1, "model": 1}
    docs = list(_products_collection().find({"shopname": shop_name.strip()}, projection)
                .limit(1000).max_time_ms(5000))
    per_label: dict[str, list[tuple[dict[str, Any], list[dict[str, Any]]]]] = {name: [] for name in requested}
    for doc in docs:
        title_matches = matches(str(doc.get("item_name") or ""))
        if not title_matches:
            continue
        title_models = best_matches(str(doc.get("item_name") or ""))
        all_units = [unit for unit in doc.get("model") or [] if isinstance(unit, dict)]
        unit_models = {label for unit in all_units for label in best_matches(str(unit.get("model_name") or ""))}
        has_distinct_models = len(unit_models) > 1
        # A title may explicitly list multiple models; unit labels then decide which unit belongs to which model.
        for label in requested:
            relevant = (label in unit_models if has_distinct_models else label in title_models)
            if not relevant:
                continue
            units = ([unit for unit in all_units if label in best_matches(str(unit.get("model_name") or ""))]
                     if has_distinct_models else all_units)
            if not units:
                continue
            per_label[label].append((doc, units))

    result: list[dict[str, Any]] = []
    for label, entries in per_label.items():
        categories = Counter(str(doc.get("category_id") or "") for doc, _ in entries if doc.get("category_id"))
        best_category = categories.most_common(1)[0][0] if categories else ""
        if len(categories) > 1 and categories[best_category] == categories.most_common(2)[1][1]:
            continue
        for doc, units in entries:
            if best_category and str(doc.get("category_id") or "") != best_category:
                continue
            item_id = str(doc.get("item_id") or "").removesuffix(".0")
            item = _products_by_ids([item_id], shop_name).get(item_id)
            if not item:
                continue
            unit_ids = {str(unit.get("model_id") or "").removesuffix(".0") for unit in units}
            item["models"] = [unit for unit in item["models"] if unit["model_id"] in unit_ids]
            item["availability"] = _item_availability(item["item_status"], item["models"])
            def normalized_option(value: str) -> str:
                return re.sub(r"[^a-z0-9ก-๙]", "", value.casefold())
            selected_unit_names = [normalized_option(unit["model_name"]) for unit in item["models"]
                                   if unit.get("model_name")]
            scoped_variations = []
            for variation in item["tier_variation"]:
                options = [option for option in variation["options"]
                           if any(normalized_option(option) in unit_name or unit_name in normalized_option(option)
                                  for unit_name in selected_unit_names)]
                if options:
                    scoped_variations.append({**variation, "options": options})
            item["tier_variation"] = scoped_variations
            item["product_name"] = label
            result.append(item)
            if len(result) >= 10:
                return result
    return result


def products_for_product_mentions(mentions: list[str], shop_name: str) -> list[dict[str, Any]]:
    """Find explicitly mentioned, non-KB editions in this one shop's Shopee listings."""
    if not mentions or not shop_name.strip():
        return []

    patterns: list[str] = []
    for mention in mentions[:5]:
        tokens = re.findall(r"[a-z0-9]+|[\u0e00-\u0e7f]+", str(mention).casefold(), re.I)
        if not tokens:
            continue
        pattern = r"(?<!\w)" + r"[\W_]*".join(re.escape(token) for token in tokens) + r"(?!\w)"
        if pattern not in patterns:
            patterns.append(pattern)
    if not patterns:
        return []

    alternatives = [{field: {"$regex": pattern, "$options": "i"}}
                    for pattern in patterns for field in ("item_name", "name")]
    query = {"shopname": shop_name.strip(), "$or": alternatives}
    projection = {"_id": 0, "item_id": 1, "item_name": 1, "name": 1}
    docs = list(_products_collection().find(query, projection).limit(50).max_time_ms(5000))
    item_ids: list[str] = []
    for doc in docs:
        title = str(doc.get("item_name") or doc.get("name") or "")
        if not any(re.search(pattern, title, re.I) for pattern in patterns):
            continue
        item_id = doc.get("item_id")
        if item_id is None:
            continue
        canonical = str(int(item_id)) if isinstance(item_id, float) and item_id.is_integer() else str(item_id)
        if canonical not in item_ids:
            item_ids.append(canonical)
        if len(item_ids) >= 10:
            break
    return list(_products_by_ids(item_ids, shop_name).values()) if item_ids else []


def chat_commerce_lookup(intent: str, product_names: list[str], card_context: list[dict[str, Any]], *,
                         platform: str, shop_id: str, shop_name: str,
                         allowed_shops: list[dict[str, Any]], catalog: list[dict[str, Any]],
                         product_mentions: list[str] | None = None) -> dict[str, Any]:
    """Resolve public chat stock questions using only the configured Shopee shop."""
    if intent not in {"stock_availability", "product_info", "product_search", "comparison", "compatibility"}:
        return {}
    scope = _scope_by_key(allowed_shops, platform, shop_id)
    same_name_scopes = [candidate for candidate in allowed_shops
                        if str(candidate.get("platform") or "").casefold() == platform.casefold()
                        and str(candidate.get("shop_name") or "").casefold() == shop_name.casefold()]
    if (not scope or platform.casefold() != "shopee" or not shop_name.strip()
            or len(same_name_scopes) != 1):
        return {"unavailable": True}
    try:
        mongo_shop_name = resolve_legacy_shop_name(platform, shop_id)
        if not mongo_shop_name:
            return {"unavailable": True}
        item_ids = list(dict.fromkeys(str(card.get("item_id") or "").strip()
                                      for card in card_context if card.get("item_id")))[:20]
        if item_ids:
            products = list(_products_by_ids(item_ids, mongo_shop_name).values())
        else:
            products = products_for_catalog_names(product_names[:10], catalog, mongo_shop_name)
            from .product_identity import match_catalog_product
            unknown_mentions = [mention for mention in (product_mentions or [])
                                if not match_catalog_product(mention, catalog)]
            products.extend(products_for_product_mentions(unknown_mentions, mongo_shop_name))
        unique = {str(item.get("item_id") or ""): item for item in products if item.get("item_id")}
        return {"products": list(unique.values())[:10]}
    except Exception as exc:
        logger.warning("public_chat_commerce_lookup_failed type=%s", type(exc).__name__)
        return {"unavailable": True}


def get_customer_summary(customer_id: str, platform: str = "shopee") -> dict[str, Any] | None:
    if not customer_id:
        return None
    name = os.getenv("LEGACY_CUSTOMERS_COLLECTION", os.getenv("ADMIN_MONGO_COLLECTION_CUSTOMERS", "customers_shp"))
    return _collection(_admin_db(), "LEGACY_CUSTOMERS_COLLECTION", name).find_one(
        {"customer_id": customer_id, "platform": platform},
        {"_id": 0, "customer_id": 1, "platform": 1, "conversations": 1,
         "shops": 1, "last_active_at": 1, "created_at": 1})
