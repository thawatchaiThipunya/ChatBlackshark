"""Exact, read-only Shopee listing lookup for a customer product-card turn."""
from __future__ import annotations

import os
from typing import Any


_client: Any = None


def lookup_shopee_item(item_id: str) -> dict[str, Any] | None:
    global _client
    uri = os.getenv("MONGO_URI", "")
    if not uri or not item_id.isdigit():
        return None
    if _client is None:
        from pymongo import MongoClient
        _client = MongoClient(uri, serverSelectionTimeoutMS=5000, socketTimeoutMS=5000)
    collection = _client[os.getenv("MONGO_DB", "dbWallet")][os.getenv("MONGO_COLLECTION", "ShpProducts")]
    values: list[Any] = [item_id]
    try:
        numeric_id = int(item_id)
        values.append(numeric_id)
        if numeric_id <= 2**53:
            values.append(float(numeric_id))
    except ValueError:
        pass
    item = collection.find_one({"item_id": {"$in": values}},
                               {"_id": 0, "item_id": 1, "item_name": 1, "name": 1,
                                "description": 1, "shopname": 1, "shop": 1,
                                "brand": 1, "image_url": 1, "images": 1})
    if not item:
        return None
    identity = " ".join(str(item.get(k) or "") for k in ("shopname", "shop", "brand")).lower()
    if "black shark" not in identity and "blackshark" not in identity:
        return None
    stored_id = item.get("item_id")
    canonical_id = str(int(stored_id)) if isinstance(stored_id, float) and stored_id.is_integer() else str(stored_id)
    images = item.get("images")
    if isinstance(images, dict):
        images = images.get("image_url_list")
    image_list = images[:5] if isinstance(images, list) else []
    primary_image = item.get("image_url")
    if not isinstance(primary_image, str):
        primary_image = next((x for x in image_list if isinstance(x, str)), "")
    return {"item_id": canonical_id,
            "name": str(item.get("item_name") or item.get("name") or "")[:300],
            "description": str(item.get("description") or "")[:8000],
            "image_url": primary_image[:2048],
            "images": [x[:2048] for x in image_list if isinstance(x, str)]}
