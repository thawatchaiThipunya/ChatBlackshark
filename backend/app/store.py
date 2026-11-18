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

    def chat_action(self, guest_token: str, action: str, session_id: str | None = None,
                    payload: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise RuntimeError("Chat store is not configured")
        return self._request("rpc/bot_chat_action", {
            "p_app_token": self.token, "p_guest_token": guest_token,
            "p_action": action, "p_session_id": session_id,
            "p_payload": payload or {},
        })

    def admin_action(self, session_token: str | None, action: str,
                     payload: dict[str, Any] | None = None) -> Any:
        if not self.token:
            raise RuntimeError("Admin store is not configured")
        return self._request("rpc/bot_admin_action", {
            "p_app_token": self.token,
            "p_session_token": session_token,
            "p_action": action,
            "p_payload": payload or {},
        })

    def storage_request(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        secret = os.getenv("SUPABASE_SECRET_KEY", "")
        if not secret:
            raise RuntimeError("Supabase Storage secret key is not configured")
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(self.url + "/storage/v1/" + path, data=data,
            headers={"apikey": secret, "Authorization": "Bearer " + secret,
                     "Content-Type": "application/json"},
            method="POST" if data is not None else "GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response) if payload is not None else response.read()

    def signed_upload(self, object_path: str) -> dict[str, Any]:
        return self.storage_request("object/upload/sign/chat-media/" + urllib.parse.quote(object_path), {})

    def media_bytes(self, object_path: str) -> bytes:
        return self.storage_request("object/chat-media/" + urllib.parse.quote(object_path))

    def list_keys(self, session_token: str) -> list[dict[str, Any]]:
        return self.admin_action(session_token, "list_keys")

    def add_key(self, label: str, key: str, session_token: str) -> dict[str, Any]:
        clean = key.strip()
        if not clean or not label.strip() or len(label) > 80:
            raise ValueError("label and key are required")
        ciphertext = Fernet(self.encryption_key.encode()).encrypt(clean.encode()).decode()
        return self.admin_action(session_token, "add_key", {
            "label": label.strip(), "ciphertext": ciphertext, "last4": clean[-4:],
        })

    def delete_key(self, key_id: int, session_token: str) -> dict[str, Any]:
        return self.admin_action(session_token, "delete_key", {"id": key_id})

    def next_key(self) -> tuple[str | None, int | None, int]:
        selected = self.key_action("next")
        if not selected.get("count"):
            return None, None, 0
        key = Fernet(self.encryption_key.encode()).decrypt(selected["ciphertext"].encode()).decode()
        return key, selected["id"], selected["count"]

    def escalate(self, session_id: str, model_name: str, question: str,
                 transcript: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Record an unresolved-issue case for staff; returns {"id": n} or None."""
        if not self.token:
            return None
        clean_turns = [
            {"role": turn.get("role", "user"), "content": str(turn.get("content", ""))[:1000]}
            for turn in (transcript or [])[-12:]
        ]
        return self._request("rpc/bot_escalate", {
            "p_token": self.token,
            "p_session_id": str(session_id or "")[:100],
            "p_model_name": str(model_name or "")[:200],
            "p_question": str(question or "")[:1000],
            "p_transcript": clean_turns,
        })
