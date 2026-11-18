from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


DATA_FILE = Path(__file__).resolve().parents[1] / "data" / "knowledge_base.json"


@lru_cache(maxsize=1)
def load_knowledge_base() -> dict[str, Any]:
    if not DATA_FILE.is_file():
        raise RuntimeError(
            "Knowledge base is missing. Run backend/scripts/prepare_knowledge_base.py first."
        )
    with DATA_FILE.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    required = {"products", "support_models", "faqs"}
    if not required.issubset(data):
        raise RuntimeError("Knowledge base does not contain the required source sections.")
    return data
