"""Bounded normalization of web and Shopee style chat events."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NormalizedChatInput:
    message_type: str
    text: str = ""
    item_ids: tuple[str, ...] = ()
    order_sn: str | None = None
    media: tuple[dict[str, str], ...] = ()
    children: tuple["NormalizedChatInput", ...] = ()
    metadata: dict[str, object] = field(default_factory=dict)


ALIASES = {"item_card": "item", "picture": "image", "faq_liveagents": "faq_liveagent",
           "bundle_deal": "bundle_message"}
KNOWN = {"text", "image", "image_with_text", "video", "sticker", "item", "variation_card",
         "order", "bundle_message", "notification", "faq_liveagent", "rating_card"}
PLACEHOLDERS = {"[image]", "[picture]", "[รูปภาพ]", "[video]", "[วิดีโอ]", "[วิดิโอ]",
                "[sticker]", "[สติกเกอร์]", "[product]", "[item]", "[item_card]",
                "[variation_card]", "[bundle_message]", "[order]", "[คำสั่งซื้อ]",
                "[notification]", "[แจ้งเตือน]", "[faq_liveagent]", "[faq_liveagents]",
                "[rating_card]", "[การ์ดคะแนน]", "[image_with_text]", "[bundle_deal]",
                "[itemid]", "[สินค้า]"}
ITEM_ID_LIST = re.compile(r"\d{8,20}(?:[\s,;，；]+\d{8,20})*")
PLACEHOLDER_TYPES = {
    "[image]": "image", "[picture]": "image", "[รูปภาพ]": "image",
    "[video]": "video", "[วิดีโอ]": "video", "[วิดิโอ]": "video",
    "[sticker]": "sticker", "[สติกเกอร์]": "sticker", "[item]": "item",
    "[item_card]": "item", "[itemid]": "item", "[สินค้า]": "item",
    "[variation_card]": "variation_card", "[bundle_message]": "bundle_message",
    "[bundle_deal]": "bundle_message", "[order]": "order", "[คำสั่งซื้อ]": "order",
    "[notification]": "notification", "[แจ้งเตือน]": "notification",
    "[faq_liveagent]": "faq_liveagent", "[faq_liveagents]": "faq_liveagent",
    "[rating_card]": "rating_card", "[การ์ดคะแนน]": "rating_card",
    "[image_with_text]": "image_with_text",
}


def _id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    raw = str(value).strip()
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]
    return raw[:100] if raw.isdigit() else ""


def _ids(value: Any) -> list[str]:
    """Read one or more IDs only from structured item ID fields."""
    if isinstance(value, (list, tuple)):
        values = value
    elif isinstance(value, str) and ITEM_ID_LIST.fullmatch(value.strip()):
        values = re.split(r"[\s,;，；]+", value.strip())
    else:
        values = [value]
    result: list[str] = []
    for candidate in values:
        item_id = _id(candidate)
        if item_id and item_id not in result:
            result.append(item_id)
    return result


def normalize_chat_input(raw_payload: object | None, fallback_text: str = "") -> NormalizedChatInput:
    raw = raw_payload if isinstance(raw_payload, dict) else {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    envelope = data.get("content") if isinstance(data.get("content"), dict) else raw
    if not envelope.get("message_type") and isinstance(envelope.get("content"), dict):
        nested_envelope = envelope["content"]
        if nested_envelope.get("message_type") or nested_envelope.get("msg_type"):
            envelope = nested_envelope
    content = envelope.get("content") if isinstance(envelope.get("content"), dict) else (
        raw.get("content") if isinstance(raw.get("content"), dict) else {})
    raw_type = (envelope.get("message_type") or envelope.get("msg_type") or envelope.get("type")
                or raw.get("message_type") or raw.get("msg_type") or raw.get("type") or "text")
    raw_type = str(raw_type).strip().lower()
    typ = ALIASES.get(raw_type, raw_type)
    typ = typ if typ in KNOWN else "unknown"
    text = str(content.get("text") or envelope.get("text") or raw.get("text") or fallback_text or "").strip()[:1000]
    placeholder_type = PLACEHOLDER_TYPES.get(text.lower())
    if typ in {"text", "unknown"} and placeholder_type:
        typ = placeholder_type
    if text.lower() in PLACEHOLDERS:
        text = ""
    ids = []
    source_content = next((source.get("source_content") for source in (content, envelope, data, raw)
                           if isinstance(source.get("source_content"), dict)
                           and source.get("source_content")), {})
    sources = (raw, data, envelope, content, source_content)
    for source in sources:
        for field_name in ("item_id", "item_ids", "product_id"):
            for item_id in _ids(source.get(field_name)):
                if item_id not in ids:
                    ids.append(item_id)
    media = []
    for source in (raw, data, envelope, content):
        for key in ("image_url", "video_url", "url", "object_path", "thumb_url", "thumbnail_url"):
            value = source.get(key)
            if isinstance(value, str) and value and len(value) <= 2048:
                media.append({key: value})
        for value in source.get("images", []) if isinstance(source.get("images"), list) else []:
            if isinstance(value, str) and len(value) <= 2048:
                media.append({"image_url": value})
    raw_children = envelope.get("children") or content.get("children") or raw.get("children") or []
    children = tuple(normalize_chat_input(child) for child in raw_children[:10] if isinstance(child, dict)) if isinstance(raw_children, list) else ()
    if typ == "bundle_message":
        for child in children:
            for item_id in child.item_ids:
                if item_id not in ids:
                    ids.append(item_id)
        if not text:
            text = " ".join(child.text for child in children if child.text)[:1000]
    metadata: dict[str, object] = {}
    if typ == "bundle_message":
        raw_messages = content.get("messages") or envelope.get("messages")
        if isinstance(raw_messages, list):
            metadata["bundle_message_ids"] = [str(value)[:100] for value in raw_messages[:20]
                if isinstance(value, (str, int)) and not isinstance(value, bool)]
    if typ == "notification":
        notification_text = next((str(source.get(key)).strip() for source in (content, source_content, envelope, raw)
            for key in ("notification_for_receiver", "notification_for_sender", "notification_text")
            if source.get(key)), "")
        if notification_text:
            metadata["notification_text"] = notification_text[:1000]
    if typ == "sticker":
        sticker = content.get("sticker") if isinstance(content.get("sticker"), dict) else {}
        for metadata_key, fields in {
            "sticker_id": ("sticker_id", "sid"),
            "sticker_package_id": ("sticker_package_id", "package_id", "pid"),
            "sticker_region": ("region", "region_code"),
            "sticker_extension": ("extension", "ext"),
        }.items():
            value = next((source.get(field_name) for source in (content, sticker, envelope, data, raw)
                          for field_name in fields if source.get(field_name) not in (None, "")), None)
            if value is not None:
                metadata[metadata_key] = str(value).strip()[:100]
    if typ == "text" and not ids and ITEM_ID_LIST.fullmatch(text):
        ids = _ids(text)
        typ = "item"
        metadata["plain_item_ids"] = True
    elif typ == "text" and ids:
        typ = "item"
    order_sn = next((source.get("order_sn") for source in (content, source_content, envelope, data, raw)
                     if source.get("order_sn")), None)
    return NormalizedChatInput(typ, text, tuple(ids[:10]), str(order_sn)[:100] if order_sn else None,
                               tuple(media[:10]), children, metadata)
