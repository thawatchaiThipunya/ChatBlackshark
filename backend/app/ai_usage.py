"""Synchronous accounting for actual Gemini and OpenRouter inference calls."""
from __future__ import annotations

import json
import logging
import math
import os
import socket
import time
from contextvars import ContextVar, Token
from typing import Any
from urllib.error import HTTPError
import urllib.request


logger = logging.getLogger("blackshark.ai_usage")
_usage_reference: ContextVar[str | None] = ContextVar("ai_usage_reference", default=None)
_usage_attempt: ContextVar[int] = ContextVar("ai_usage_attempt", default=1)
_GEMINI_USAGE_FIELDS = (
    "prompt_token_count",
    "candidates_token_count",
    "total_token_count",
    "thoughts_token_count",
    "cached_content_token_count",
)


class AiUsageHubUnavailable(RuntimeError):
    """A paid model call cannot proceed or return without usage reporting."""


def require_ai_usage_hub() -> None:
    if not (os.environ.get("AI_USAGE_HUB_URL") or "").strip() or not (
            os.environ.get("AI_USAGE_HUB_TOKEN") or "").strip():
        raise AiUsageHubUnavailable("AI Usage Hub is not configured")


def set_usage_reference(reference: str | None) -> Token:
    """Bind a short request reference for model calls in this request context."""
    safe_reference = "".join(character for character in str(reference or "") if character.isalnum() or character in "._:-")[:100]
    return _usage_reference.set(safe_reference or None)


def reset_usage_reference(token: Token) -> None:
    _usage_reference.reset(token)


def set_usage_attempt(attempt: int) -> Token:
    return _usage_attempt.set(max(1, int(attempt)))


def reset_usage_attempt(token: Token) -> None:
    _usage_attempt.reset(token)


def _send_report(url: str, token: str, event: dict[str, Any]) -> bool:
    try:
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            f"{url}/internal/ai-usage/logs",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "x-service-token": token},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            status = getattr(response, "status", 200)
        if status >= 400:
            logger.warning("ai_usage_hub_rejected status=%s", status)
            return False
        logger.info("ai_usage_hub_sent provider=%s model=%s status=%s",
                    event.get("provider", "unknown"), event.get("model", "unknown"), status)
        return 200 <= status < 300
    except HTTPError as exc:
        logger.warning("ai_usage_hub_rejected status=%s", exc.code)
        return False
    except Exception as exc:
        # Never include the event, request prompt, provider response, or token in logs.
        logger.warning("ai_usage_hub_send_failed error_type=%s", type(exc).__name__)
        return False


def report_ai_usage(event: dict[str, Any]) -> bool:
    """Send one event before returning so short-lived serverless workers cannot drop it."""
    try:
        url = (os.environ.get("AI_USAGE_HUB_URL") or "").strip().rstrip("/")
        token = (os.environ.get("AI_USAGE_HUB_TOKEN") or "").strip()
        if not url or not token:
            return False

        payload = dict(event)
        payload.setdefault("environment", os.environ.get("VERCEL_ENV") or os.environ.get("APP_ENV") or "development")
        reference = _usage_reference.get()
        if reference:
            payload.setdefault("reference", f"chat_request:{reference}")
        if payload.get("source"):
            metadata = dict(payload.get("metadata") or {})
            metadata.setdefault("analysis_type", payload["source"])
            payload["metadata"] = metadata
        # Vercel may freeze a function as soon as its response completes. Sending
        # this in a background executor can silently lose paid inference records.
        return _send_report(url, token, payload)
    except Exception as exc:
        logger.warning("ai_usage_hub_report_failed error_type=%s", type(exc).__name__)
        return False


def _http_status(error: Exception | None) -> int | None:
    if error is None:
        return None
    for value in (
        getattr(error, "code", None),
        getattr(error, "status_code", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
    return None


def _status(error: Exception | None, http_status: int | None) -> str:
    if error is None:
        return "success"
    if isinstance(error, (TimeoutError, socket.timeout)) or http_status in (408, 504):
        return "timeout"
    return "error"


def report_gemini_usage(
    response: Any | None,
    *,
    model: str,
    source: str,
    duration_ms: int,
    attempt: int | None = None,
    status: str | None = None,
    error: Exception | None = None,
    units: dict[str, int | float] | None = None,
) -> bool:
    """Report Gemini's actual token metadata, without inventing per-call cost."""
    http_status = _http_status(error)
    event: dict[str, Any] = {
        "provider": "gemini",
        "model": model,
        "operation": "generateContent",
        "source": source,
        "user": "customer_chat",
        "duration_ms": max(0, int(duration_ms)),
        "attempt": attempt if attempt is not None else _usage_attempt.get(),
        "status": status or _status(error, http_status),
    }
    response_id = getattr(response, "response_id", None) if response is not None else None
    if isinstance(response_id, str) and response_id:
        event["request_id"] = response_id
    usage = getattr(response, "usage_metadata", None) if response is not None else None
    raw_usage: dict[str, int | float] = {}
    for field in _GEMINI_USAGE_FIELDS:
        value = getattr(usage, field, None) if usage is not None else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            event_key = {
                "prompt_token_count": "prompt_tokens",
                "candidates_token_count": "completion_tokens",
            }.get(field)
            if event_key:
                event[event_key] = int(value)
            raw_usage[field] = int(value)
    if raw_usage:
        event["raw_usage"] = raw_usage
    if units:
        event["units"] = units
    if http_status is not None:
        event["http_status"] = http_status
    if error is not None:
        event["error_message"] = type(error).__name__
    return report_ai_usage(event)


def call_gemini(
    client: Any,
    *,
    model: str,
    contents: Any,
    config: Any,
    source: str,
    attempt: int | None = None,
    units: dict[str, int | float] | None = None,
) -> Any:
    """Make one generateContent call and report success or failure, then preserve its result."""
    require_ai_usage_hub()
    started = time.perf_counter()
    try:
        response = client.models.generate_content(model=model, contents=contents, config=config)
    except Exception as exc:
        reported = _best_effort_report_gemini_usage(
            None,
            model=model,
            source=source,
            duration_ms=round((time.perf_counter() - started) * 1000),
            attempt=attempt,
            error=exc,
            units=units,
        )
        if not reported:
            raise AiUsageHubUnavailable("AI Usage Hub did not accept provider usage") from exc
        raise
    if not _best_effort_report_gemini_usage(
        response,
        model=model,
        source=source,
        duration_ms=round((time.perf_counter() - started) * 1000),
        attempt=attempt,
        units=units,
    ):
        raise AiUsageHubUnavailable("AI Usage Hub did not accept provider usage")
    return response


def _best_effort_report_gemini_usage(*args: Any, **kwargs: Any) -> bool:
    try:
        return report_gemini_usage(*args, **kwargs)
    except Exception as exc:
        logger.warning("ai_usage_event_build_failed provider=gemini error_type=%s", type(exc).__name__)
        return False
