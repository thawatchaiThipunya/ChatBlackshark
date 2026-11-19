from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from cryptography.fernet import Fernet


# The Gemini Models API does not report billing-tier eligibility. Keep this
# allowlist aligned with the Free Tier column in Google's pricing documentation.
GEMINI_FREE_CHAT_MODELS = {
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
}
MODEL_CATALOG_TTL_SECONDS = 900


class SupabaseStore:
    """Read public knowledge and call the token-gated key-store RPC."""

    def __init__(self, url: str | None = None, publishable_key: str | None = None,
                 token: str | None = None, encryption_key: str | None = None):
        self.url = (url or os.getenv("SUPABASE_URL", "")).rstrip("/")
        self.publishable_key = publishable_key or os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
        self.token = token or os.getenv("LLM_KEY_STORE_TOKEN", "")
        self.encryption_key = encryption_key or os.getenv("LLM_KEY_ENCRYPTION_KEY", "")
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._runtime_cache: tuple[float, dict[str, Any]] | None = None
        self._persona_cache: tuple[float, dict[str, Any]] | None = None
        self._model_catalog_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}

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
            headers={"apikey": secret, "Content-Type": "application/json"},
            method="POST" if data is not None else "GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response) if payload is not None else response.read()

    def signed_upload(self, object_path: str) -> dict[str, Any]:
        return self.storage_request("object/upload/sign/chat-media/" + urllib.parse.quote(object_path), {})

    def signed_download(self, object_path: str) -> dict[str, Any]:
        return self.storage_request("object/sign/chat-media/" + urllib.parse.quote(object_path), {"expiresIn": 900})

    def media_bytes(self, object_path: str) -> bytes:
        return self.storage_request("object/chat-media/" + urllib.parse.quote(object_path))

    def list_keys(self, session_token: str) -> list[dict[str, Any]]:
        return self.admin_action(session_token, "list_keys")

    def add_key(self, label: str, key: str, session_token: str) -> dict[str, Any]:
        clean = key.strip()
        if not clean or not label.strip() or len(label) > 80:
            raise ValueError("label and key are required")
        ciphertext = Fernet(self.encryption_key.encode()).encrypt(clean.encode()).decode()
        result = self.admin_action(session_token, "add_key", {
            "label": label.strip(), "ciphertext": ciphertext, "last4": clean[-4:],
        })
        self._model_catalog_cache.pop("gemini", None)
        return result

    def delete_key(self, key_id: int, session_token: str) -> dict[str, Any]:
        result = self.admin_action(session_token, "delete_key", {"id": key_id})
        self._model_catalog_cache.pop("gemini", None)
        return result

    def next_key(self) -> tuple[str | None, int | None, int]:
        selected = self.key_action("next")
        if not selected.get("count"):
            return None, None, 0
        key = Fernet(self.encryption_key.encode()).decrypt(selected["ciphertext"].encode()).decode()
        return key, selected["id"], selected["count"]

    def key_cycle(self) -> list[tuple[str, int]]:
        if not self.token:
            raise RuntimeError("LLM key store is not configured")
        rows = self._request("rpc/bot_llm_key_cycle", {"p_token": self.token})
        cipher = Fernet(self.encryption_key.encode())
        return [(cipher.decrypt(row["ciphertext"].encode()).decode(), row["id"]) for row in rows]

    def model_catalog(self, provider: str) -> list[dict[str, Any]]:
        if provider not in {"gemini", "openrouter"}:
            raise ValueError("unsupported model provider")
        cached = self._model_catalog_cache.get(provider)
        if cached and time.monotonic() - cached[0] < MODEL_CATALOG_TTL_SECONDS:
            return [dict(model) for model in cached[1]]

        models = self._fetch_gemini_model_catalog() if provider == "gemini" else self._fetch_openrouter_model_catalog()
        self._model_catalog_cache[provider] = (time.monotonic(), models)
        return [dict(model) for model in models]

    def _fetch_gemini_model_catalog(self) -> list[dict[str, Any]]:
        keys = self.key_cycle()
        if not keys:
            return []

        # The API returns the same public catalog for each key; retry the next
        # stored key if the selected key has expired or lost access.
        for key, _key_id in keys:
            try:
                models: dict[str, dict[str, Any]] = {}
                page_token: str | None = None
                for _ in range(10):
                    params = {"pageSize": 1000}
                    if page_token:
                        params["pageToken"] = page_token
                    request = urllib.request.Request(
                        "https://generativelanguage.googleapis.com/v1beta/models?" + urllib.parse.urlencode(params),
                        headers={"x-goog-api-key": key, "Accept": "application/json"},
                    )
                    with urllib.request.urlopen(request, timeout=15) as response:
                        payload = json.load(response)
                    for model in payload.get("models", []):
                        model_id = str(model.get("name", "")).removeprefix("models/")
                        if model_id in GEMINI_FREE_CHAT_MODELS and "generateContent" in model.get("supportedGenerationMethods", []):
                            models[model_id] = {
                                "id": model_id,
                                "name": str(model.get("displayName") or model_id),
                                "supportsVision": True,
                            }
                    page_token = payload.get("nextPageToken")
                    if not page_token:
                        break
                return sorted(models.values(), key=lambda model: model["name"].casefold())
            except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError):
                continue
        raise RuntimeError("Gemini model catalog is unavailable")

    def _fetch_openrouter_model_catalog(self) -> list[dict[str, Any]]:
        key = self.runtime_config().get("openrouter_key")
        if not key:
            return []
        request = urllib.request.Request(
            "https://openrouter.ai/api/v1/models",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("OpenRouter model catalog is unavailable") from exc

        models: dict[str, dict[str, Any]] = {}
        for model in payload.get("data", []):
            model_id = str(model.get("id", ""))
            architecture = model.get("architecture") or {}
            inputs = architecture.get("input_modalities") or []
            outputs = architecture.get("output_modalities") or []
            endpoints = model.get("endpoints") or []
            if (not model_id or "text" not in inputs or "text" not in outputs
                    or "/api/v1/chat/completions" not in endpoints):
                continue
            models[model_id] = {
                "id": model_id,
                "name": str(model.get("name") or model_id),
                "supportsVision": "image" in inputs,
            }
        return sorted(models.values(), key=lambda model: model["name"].casefold())

    def llm_config_action(self, action: str, session_token: str | None = None,
                          payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("LLM configuration store is not configured")
        return self._request("rpc/bot_llm_config_action", {
            "p_app_token": self.token, "p_session_token": session_token,
            "p_action": action, "p_payload": payload or {},
        })

    def runtime_config(self) -> dict[str, Any]:
        if self._runtime_cache and time.monotonic() - self._runtime_cache[0] < 15:
            return self._runtime_cache[1]
        config = self.llm_config_action("get_runtime")
        if config.get("openrouter_key_ciphertext"):
            config["openrouter_key"] = Fernet(self.encryption_key.encode()).decrypt(
                config.pop("openrouter_key_ciphertext").encode()).decode()
        self._runtime_cache = (time.monotonic(), config)
        return config

    def save_llm_config(self, session_token: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.llm_config_action("save_config", session_token, payload)
        self._runtime_cache = None
        return result

    def set_openrouter_key(self, session_token: str, key: str) -> dict[str, Any]:
        clean = key.strip()
        if len(clean) < 10:
            raise ValueError("OpenRouter key is too short")
        ciphertext = Fernet(self.encryption_key.encode()).encrypt(clean.encode()).decode()
        result = self.llm_config_action("set_openrouter_key", session_token,
            {"ciphertext": ciphertext, "last4": clean[-4:]})
        self._runtime_cache = None
        self._model_catalog_cache.pop("openrouter", None)
        return result

    def persona_action(self, action: str, session_token: str | None = None,
                       payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.token:
            raise RuntimeError("Persona store is not configured")
        return self._request("rpc/bot_persona_action", {
            "p_app_token": self.token, "p_session_token": session_token,
            "p_action": action, "p_payload": payload or {},
        })

    def persona(self) -> dict[str, Any]:
        if self._persona_cache and time.monotonic() - self._persona_cache[0] < 15:
            return self._persona_cache[1]
        config = self.persona_action("get_runtime")
        self._persona_cache = (time.monotonic(), config)
        return config

    def save_persona(self, session_token: str, payload: dict[str, str]) -> dict[str, Any]:
        config = self.persona_action("save", session_token, payload)
        self._persona_cache = None
        return config

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
