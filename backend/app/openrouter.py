"""Small OpenRouter chat-completions client for text and multimodal requests."""
from __future__ import annotations

import base64
import json
import logging
import math
import socket
import time
from typing import Any
from urllib.error import HTTPError
import urllib.request

from .ai_usage import report_ai_usage


URL = "https://openrouter.ai/api/v1/chat/completions"
logger = logging.getLogger("blackshark.ai_usage")


def complete(key: str, model: str, content: str | list[dict], max_tokens: int = 1200, *,
             source: str = "chat.openrouter", units: dict[str, int | float] | None = None) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": max_tokens,
        "usage": {"include": True},
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://chat-blackshark-web.vercel.app",
        "X-Title": "Black Shark Chat",
    })
    started = time.perf_counter()
    data: dict[str, Any] = {}
    http_status = None
    reported = False
    try:
        with urllib.request.urlopen(request, timeout=75) as response:
            http_status = getattr(response, "status", None)
            data = json.load(response)
        message = data["choices"][0]["message"]["content"]
        if isinstance(message, list):
            message = "".join(part.get("text", "") for part in message if isinstance(part, dict))
        if not isinstance(message, str) or not message.strip():
            raise RuntimeError("OpenRouter returned no content")
        _best_effort_report_usage(data, model, source, round((time.perf_counter() - started) * 1000),
                                  "success", http_status, units)
        reported = True
        return message.strip()
    except Exception as exc:
        if not reported:
            if http_status is None and isinstance(exc, HTTPError):
                http_status = exc.code
            status = "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) or http_status in (408, 504) else "error"
            _best_effort_report_usage(data, model, source, round((time.perf_counter() - started) * 1000),
                                      status, http_status, units, exc)
        raise


def _best_effort_report_usage(*args: Any, **kwargs: Any) -> None:
    try:
        _report_usage(*args, **kwargs)
    except Exception as exc:
        # Reporting must never change the provider result or error seen by chat.
        logger.warning("ai_usage_event_build_failed provider=openrouter error_type=%s", type(exc).__name__)


def _report_usage(data: dict[str, Any], model: str, source: str, duration_ms: int,
                  status: str, http_status: int | None, units: dict[str, int | float] | None,
                  error: Exception | None = None) -> None:
    data = data if isinstance(data, dict) else {}
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    actual_model = data.get("model") if isinstance(data.get("model"), str) else model
    event: dict[str, Any] = {
        "provider": "openrouter",
        "model": actual_model,
        "operation": "chat.completions",
        "source": source,
        "user": "customer_chat",
        "duration_ms": max(0, int(duration_ms)),
        "attempt": 1,
        "status": status,
    }
    request_id = data.get("id")
    if isinstance(request_id, str) and request_id:
        event["request_id"] = request_id
    for source_field, event_field in (("prompt_tokens", "prompt_tokens"),
                                      ("completion_tokens", "completion_tokens")):
        value = usage.get(source_field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            event[event_field] = value
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0:
        event["cost_usd"] = cost
    raw_usage: dict[str, Any] = {}
    for field in ("prompt_tokens", "completion_tokens", "total_tokens", "cost", "is_byok"):
        value = usage.get(field)
        if isinstance(value, (int, float, bool)) and not isinstance(value, complex):
            if not isinstance(value, float) or math.isfinite(value):
                raw_usage[field] = value
    for field in ("prompt_tokens_details", "completion_tokens_details", "cost_details"):
        details = usage.get(field)
        if not isinstance(details, dict):
            continue
        safe_details = {
            key: value
            for key, value in details.items()
            if isinstance(key, str)
            and len(key) <= 48
            and isinstance(value, (int, float, bool))
            and not isinstance(value, complex)
            and (not isinstance(value, float) or math.isfinite(value))
        }
        if safe_details:
            raw_usage[field] = safe_details
    if raw_usage:
        event["raw_usage"] = raw_usage
    if units:
        event["units"] = units
    if http_status is not None:
        event["http_status"] = http_status
    if error is not None:
        event["error_message"] = type(error).__name__
    report_ai_usage(event)


def media_part(data: bytes, mime_type: str) -> dict:
    if len(data) > 12 * 1024 * 1024:
        raise ValueError("OpenRouter media must be 12 MB or smaller")
    url = f"data:{mime_type};base64,{base64.b64encode(data).decode()}"
    if mime_type.startswith("video/"):
        return {"type": "video_url", "video_url": {"url": url}}
    return {"type": "image_url", "image_url": {"url": url}}
