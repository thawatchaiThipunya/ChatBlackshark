"""Resolve a customer card's identity; the shop KB supplies all product facts."""
from __future__ import annotations

from typing import Any


def lookup_shopee_item(item_id: str, *, platform: str, shop_id: str,
                       shop_name: str) -> dict[str, Any] | None:
    platform = platform.strip().casefold()
    shop_id = shop_id.strip()
    if platform != "shopee" or not shop_id or not str(item_id).isdigit():
        return None

    from . import legacy_data

    legacy_shop_name = legacy_data.resolve_legacy_shop_name(platform, shop_id)
    if not legacy_shop_name:
        return None
    return legacy_data.product_identities_by_ids([str(item_id)], legacy_shop_name).get(str(item_id))
