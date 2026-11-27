"""Conservative matching between exact marketplace listing names and KB models."""
from __future__ import annotations

import re
from typing import Any


_TYPE_LINE = re.compile(r"^(?:ชื่อรุ่นสินค้าย่อ|รุ่นย่อ|รุ่นสินค้า):\s*(.+)$", re.I)
_GENERIC_PHRASES = (
    "smart watch", "smartwatch", "watch", "new arrival", "newarrival",
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


def _contains_catalog_alias(listing_words: tuple[str, ...], products: list[dict[str, Any]]) -> bool:
    return any(
        len(alias) >= 1 and any(
            listing_words[index:index + len(alias)] == alias
            for index in range(len(listing_words) - len(alias) + 1)
        )
        for product in products
        for alias in _aliases(product)
    )


def _is_composite_listing(listing_name: str, products: list[dict[str, Any]]) -> bool:
    """Treat operators as bundle separators only when their operands name products.

    Marketplace titles also use ampersands and multiplication signs between
    specifications (for example, water-resistance ratings), so the operator by
    itself is not proof that the listing contains multiple products.
    """
    for match in re.finditer(r"[+&×]", listing_name):
        left, right = listing_name[:match.start()], listing_name[match.end():]
        left_has_product = _contains_catalog_alias(_model_words(left), products)
        right_has_product = _contains_catalog_alias(_model_words(right), products)
        # A plus immediately after a number is commonly a feature count (e.g.
        # "150+ sports modes"), not a bundle separator.
        if (match.group() == "+" and match.start() > 0
                and listing_name[match.start() - 1].isdigit() and not right_has_product):
            continue
        if match.group() == "+" and (left_has_product or right_has_product):
            return True
        if match.group() in "&×" and left_has_product and right_has_product:
            return True
    return False


def match_catalog_product(listing_name: str, products: list[dict[str, Any]],
                          item_id: str | int | None = None) -> str | None:
    """Map a listing title to one unique normalized product alias in the KB.

    Exact aliases win. A shortened listing title may match the end of a longer
    KB alias only when that result is unique across the shop catalog. Longer
    marketplace titles can be mapped when their extra terms are supported by
    the KB description. Unsupported suffixes stay unresolved instead of inheriting
    a base model's facts.
    """
    listing_words = _model_words(listing_name)
    id_matches: list[dict[str, Any]] = []
    if item_id is not None:
        requested_id = str(item_id).strip()
        for product in products:
            raw_ids = product.get("item_ids")
            values = raw_ids if isinstance(raw_ids, (list, tuple, set)) else [raw_ids]
            if requested_id and any(str(value).strip() == requested_id for value in values if value is not None):
                id_matches.append(product)
        id_names = list(dict.fromkeys(str(item.get("name") or "") for item in id_matches
                                      if str(item.get("name") or "")))
        if id_matches:
            # Use the card title as a consistency check for the ID crosswalk.
            # A stale/misassigned item_id must not override an explicit model or
            # edition in the title. If the title contains no known KB alias, one
            # unique shop-scoped ID mapping is still a useful identity signal.
            title_match = match_catalog_product(listing_name, products)
            contains_known_alias = _contains_catalog_alias(listing_words, products)
            if title_match in id_names:
                return title_match
            if title_match is not None or contains_known_alias:
                return None
            if len(id_names) == 1:
                return id_names[0]
            # Some catalogs reuse one item ID across editions; with no title
            # evidence that resolves the ambiguity, keep the card unresolved.
            return None

    if not listing_words:
        return None
    composite_listing = _is_composite_listing(listing_name, products)
    exact_matches: list[str] = []
    suffix_matches: list[str] = []
    supported_title_matches: list[tuple[str, int, int, float]] = []
    for product in products:
        name = str(product.get("name") or "")
        aliases = _aliases(product)
        for alias in _aliases(product):
            if alias == listing_words:
                exact_matches.append(name)
                break
            if len(alias) > len(listing_words) and alias[-len(listing_words):] == listing_words:
                suffix_matches.append(name)
        else:
            # A listing title can append supported feature text to a model name.
            # Only use this broader match for one-product titles; '+' and similar
            # separators indicate a composite item that must resolve as a whole.
            # Treat a spaced operator or a tight word/model-code join as a
            # composite separator. A trailing quantity marker such as "150+"
            # is ordinary listing text and must not block identity matching.
            if composite_listing:
                continue
            contained_aliases = [alias for alias in aliases if len(alias) < len(listing_words)
                                 and any(listing_words[index:index + len(alias)] == alias
                                         for index in range(len(listing_words) - len(alias) + 1))]
            if not contained_aliases:
                continue
            longest_alias = max(contained_aliases, key=len)
            start = next(index for index in range(len(listing_words) - len(longest_alias) + 1)
                         if listing_words[index:index + len(longest_alias)] == longest_alias)
            remaining = listing_words[:start] + listing_words[start + len(longest_alias):]
            description_words = set(_model_words(str(product.get("description") or "")))
            remaining_words = set(remaining)
            supported_words = remaining_words & description_words
            if len(supported_words) >= 2 and len(supported_words) / max(len(remaining_words), 1) >= 0.15:
                supported_title_matches.append((name, len(longest_alias), len(supported_words),
                                                len(supported_words) / len(remaining_words)))
    matches = exact_matches or suffix_matches
    if matches:
        winners = list(dict.fromkeys(matches))
        return winners[0] if len(winners) == 1 else None
    if supported_title_matches:
        best_score = max((alias_length, support_ratio, support_count)
                         for _, alias_length, support_count, support_ratio in supported_title_matches)
        winners = list(dict.fromkeys(name for name, alias_length, support_count, support_ratio
                                     in supported_title_matches
                                     if (alias_length, support_ratio, support_count) == best_score))
        if len(winners) == 1:
            return winners[0]

    return None
