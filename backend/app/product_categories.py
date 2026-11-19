"""Read only the product category explicitly supplied in the KB source."""
from __future__ import annotations


def category_from_description(description: str | None) -> str | None:
    for line in (description or "").splitlines():
        line = line.strip()
        if not line:
            continue
        for prefix in ("ประเภทสินค้า:", "ประเภท:"):
            if line.casefold().startswith(prefix.casefold()):
                value = line[len(prefix):].strip()
                return value or None
        return None
    return None
