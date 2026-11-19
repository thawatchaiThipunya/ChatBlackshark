"""Conservative matching between exact marketplace listing names and KB models."""
from __future__ import annotations

import re
from typing import Any


_TYPE_LINE = re.compile(r"^(?:ชื่อรุ่นสินค้าย่อ|รุ่นย่อ|รุ่นสินค้า):\s*(.+)$", re.I)
_GENERIC_PHRASES = (
    "black shark", "smart watch", "smartwatch", "watch", "new arrival", "newarrival",
    "global version", "official store", "official", "global", "version",
)
_SKU_CAPACITY = re.compile(r"\b\d+\s*/\s*\d+\s*(?:gb|tb)\b", re.I)
_WORD = re.compile(r"[a-z0-9]+|[\u0e00-\u0e7f]+", re.I)


def _model_words(value: str) -> tuple[str, ...]:
    normalized = _SKU_CAPACITY.sub(" ", value.casefold())
    for phrase in _GENERIC_PHRASES:
        normalized = re.sub(rf"(?<![\w]){re.escape(phrase)}(?![\w])", " ", normalized, flags=re.I)
    return tuple(_WORD.findall(normalized))


def _aliases(product: dict[str, Any]) -> list[tuple[str, ...]]:
    candidates = [str(product.get("name") or "")]
    for line in str(product.get("description") or "").splitlines():
        match = _TYPE_LINE.match(line.strip())
        if match:
            candidates.extend(part.strip() for part in match.group(1).split(","))
    return list(dict.fromkeys(words for value in candidates if (words := _model_words(value))))


def match_catalog_product(listing_name: str, products: list[dict[str, Any]]) -> str | None:
    """Match a card only when its meaningful model tokens exactly equal a KB alias.

    Generic store/category descriptors and storage-size suffixes can be ignored.
    Unrecognized edition/model qualifiers remain significant, so an unknown
    ``S3 Special Edition`` will never silently become the base ``S3``.
    """
    listing_words = _model_words(listing_name)
    if not listing_words:
        return None
    matches: list[tuple[int, str]] = []
    for product in products:
        name = str(product.get("name") or "")
        for alias in _aliases(product):
            if alias == listing_words:
                matches.append((len(alias), name))
                break
    if not matches:
        return None
    longest = max(length for length, _ in matches)
    winners = list(dict.fromkeys(name for length, name in matches if length == longest))
    return winners[0] if len(winners) == 1 else None
