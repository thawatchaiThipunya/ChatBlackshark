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
         "order", "bundle_message", "notification", "faq_liveagent"}
PLACEHOLDERS = {"[image]", "[picture]", "[รูปภาพ]", "[video]", "[วิดีโอ]", "[วิดิโอ]",
                "[sticker]", "[สติกเกอร์]", "[product]", "[item]", "[item_card]",
                "[variation_card]", "[bundle_message]", "[order]", "[คำสั่งซื้อ]"}
ITEM_ID_LIST = re.compile(r"\d{8,20}(?:[\s,;，；]+\d{8,20})*")


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
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    typ = ALIASES.get(str(raw.get("message_type") or raw.get("type") or "text").lower(),
                      str(raw.get("message_type") or raw.get("type") or "text").lower())
    typ = typ if typ in KNOWN else "unknown"
    text = str(raw.get("text") or content.get("text") or fallback_text or "").strip()[:1000]
    if text.lower() in PLACEHOLDERS:
        text = ""
    ids = []
    for source in (raw, content, content.get("source_content") if isinstance(content.get("source_content"), dict) else {}):
        for field_name in ("item_id", "item_ids"):
            for item_id in _ids(source.get(field_name)):
                if item_id not in ids:
                    ids.append(item_id)
    media = []
    for source in (raw, content):
        for key in ("image_url", "video_url", "url", "object_path"):
            value = source.get(key)
            if isinstance(value, str) and value and len(value) <= 2048:
                media.append({key: value})
        for value in source.get("images", []) if isinstance(source.get("images"), list) else []:
            if isinstance(value, str) and len(value) <= 2048:
                media.append({"image_url": value})
    raw_children = raw.get("children") or content.get("children") or []
    children = tuple(normalize_chat_input(child) for child in raw_children[:10] if isinstance(child, dict)) if isinstance(raw_children, list) else ()
    if typ == "bundle_message":
        for child in children:
            for item_id in child.item_ids:
                if item_id not in ids:
                    ids.append(item_id)
        if not text:
            text = " ".join(child.text for child in children if child.text)[:1000]
    metadata: dict[str, object] = {}
    if typ == "text" and not ids and ITEM_ID_LIST.fullmatch(text):
        ids = _ids(text)
        typ = "item"
        metadata["plain_item_ids"] = True
    elif typ == "text" and ids:
        typ = "item"
    order_sn = raw.get("order_sn") or content.get("order_sn")
    return NormalizedChatInput(typ, text, tuple(ids[:10]), str(order_sn)[:100] if order_sn else None,
                               tuple(media[:5]), children, metadata)
