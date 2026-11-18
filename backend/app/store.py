from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cryptography.fernet import Fernet


class SupabaseStore:
    """Read public knowledge and call the token-gated key-store RPC."""

    def __init__(self, url: str | None = None, publishable_key: str | None = None,
                 token: str | None = None, encryption_key: str | None = None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.publishable_key = publishable_key or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        self.token = token or os.getenv("LLM_KEY_STORE_TOKEN", "")
        self.encryption_key = encryption_key or os.getenv("LLM_KEY_ENCRYPTION_KEY", "")
        self._cache: tuple[float, dict[str, Any]] | None = None

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.url or not self.publishable_key:
            raise RuntimeError("Supabase is not configured")
        headers = {"apikey": self.publishable_key, "Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(self.url + "/rest/v1/" + path, data=data, headers=headers,
                                         method="POST" if data is not None else "GET")
        with urllib.request.urlopen(request, timeout=12) as response:
            return json.load(response)

    def knowledge(self) -> dict[str, Any]:
        if self._cache and time.monotonic() - self._cache[0] < 60:
            return self._cache[1]
        products = self._request("bot_products?select=name,description,source&order=source_index.asc")
        faqs = self._request("bot_faqs?select=model_name,question,answer,source&order=source_index.asc&limit=1000")
        if not products or not faqs:
            raise RuntimeError("Supabase knowledge is empty")
        data = {"products": products, "faqs": faqs,
                "support_models": sorted({item["model_name"] for item in faqs})}
        self._cache = (time.monotonic(), data)
        return data

    def key_action(self, action: str, **kwargs: Any) -> Any:
        if not self.token:
            raise RuntimeError("LLM key store is not configured")
        return self._request("rpc/bot_llm_key_action", {"p_token": self.token, "p_action": action, **kwargs})

    def list_keys(self) -> list[dict[str, Any]]:
        return self.key_action("list")

    def add_key(self, label: str, key: str) -> dict[str, Any]:
        clean = key.strip()
        if not clean or not label.strip() or len(label) > 80:
            raise ValueError("label and key are required")
        ciphertext = Fernet(self.encryption_key.encode()).encrypt(clean.encode()).decode()
        return self.key_action("add", p_label=label.strip(), p_ciphertext=ciphertext, p_last4=clean[-4:])

    def delete_key(self, key_id: int) -> dict[str, Any]:
        return self.key_action("delete", p_id=key_id)

    def next_key(self) -> tuple[str | None, int | None, int]:
        selected = self.key_action("next")
        if not selected.get("count"):
            return None, None, 0
        key = Fernet(self.encryption_key.encode()).decrypt(selected["ciphertext"].encode()).decode()
        return key, selected["id"], selected["count"]
