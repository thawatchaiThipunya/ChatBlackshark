"""Exact, read-only Shopee listing lookup for a customer product-card turn."""
from __future__ import annotations

import os
from typing import Any


_client: Any = None


def lookup_shopee_item(item_id: str, *, platform: str, shop_id: str,
                       shop_name: str) -> dict[str, Any] | None:
    platform = platform.strip().casefold()
    shop_id = shop_id.strip()
    shop_name = shop_name.strip()
    if platform != "shopee" or not shop_id or not shop_name:
        return None
    global _client
    uri = os.getenv("MONGO_URI", "")
    if not uri or not item_id.isdigit():
        return None
    # ShpProducts carries a legacy shopname alias; resolve it through the scoped
    # conversation/shop_id mapping instead of assuming the display names match.
    from .legacy_data import resolve_legacy_shop_name
    mongo_shop_name = resolve_legacy_shop_name(platform, shop_id)
    if not mongo_shop_name:
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
    item = collection.find_one({"item_id": {"$in": values}, "shopname": mongo_shop_name},
                               {"_id": 0, "item_id": 1, "item_name": 1, "name": 1,
                                "description": 1, "shopname": 1, "shop": 1,
                                "brand": 1, "image_url": 1, "images": 1,
                                "item_status": 1, "model": 1, "tier_variation": 1})
    if not item:
        return None
    if str(item.get("shopname") or "").strip().casefold() != mongo_shop_name.casefold():
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
    from .legacy_data import _item_availability, _model_summary, _tier_variation_summary
    models = [_model_summary(model) for model in item.get("model", []) if isinstance(model, dict)][:100]
    item_status = str(item.get("item_status") or "")
    return {"item_id": canonical_id,
            "name": str(item.get("item_name") or item.get("name") or "")[:300],
            "description": str(item.get("description") or "")[:8000],
            "image_url": primary_image[:2048],
            "images": [x[:2048] for x in image_list if isinstance(x, str)],
            "shopname": str(item.get("shopname") or ""), "item_status": item_status,
            "availability": _item_availability(item_status, models), "models": models,
            "tier_variation": _tier_variation_summary(item.get("tier_variation"))}
