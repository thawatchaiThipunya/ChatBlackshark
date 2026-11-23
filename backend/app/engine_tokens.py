"""Short-lived HMAC access tokens for the ChatBotProductMS engine bridge."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time


ACCESS_TOKEN_TTL_SECONDS = 600
TOKEN_ISSUER = "chatblackshark-api"
TOKEN_AUDIENCE = "chatblackshark-engine"
TOKEN_SUBJECT = "chatbotproductms"
TOKEN_SCOPE = "engine:answer"
MIN_SECRET_LENGTH = 32


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)


def issue_engine_access_token(signing_secret: str, *, now: int | None = None) -> str:
    if len(signing_secret) < MIN_SECRET_LENGTH:
        raise ValueError("engine token signing secret is not configured")
    issued_at = int(time.time()) if now is None else int(now)
    header = {"alg": "HS256", "typ": "JWT"}
    claims = {
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "sub": TOKEN_SUBJECT,
        "scope": TOKEN_SCOPE,
        "iat": issued_at,
        "exp": issued_at + ACCESS_TOKEN_TTL_SECONDS,
        "jti": secrets.token_urlsafe(18),
    }
    encoded_header = _encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    encoded_claims = _encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
    signature = hmac.new(signing_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_claims}.{_encode(signature)}"


def verify_engine_access_token(token: str, signing_secret: str, *, now: int | None = None) -> bool:
    if len(signing_secret) < MIN_SECRET_LENGTH or not token or len(token) > 8192:
        return False
    try:
        encoded_header, encoded_claims, encoded_signature = token.split(".")
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        expected = hmac.new(signing_secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        supplied = _decode(encoded_signature)
        if not hmac.compare_digest(expected, supplied):
            return False
        header = json.loads(_decode(encoded_header))
        claims = json.loads(_decode(encoded_claims))
        if not isinstance(header, dict) or header != {"alg": "HS256", "typ": "JWT"}:
            return False
        if not isinstance(claims, dict):
            return False
        issued_at, expires_at = claims.get("iat"), claims.get("exp")
        if (not isinstance(issued_at, int) or isinstance(issued_at, bool)
                or not isinstance(expires_at, int) or isinstance(expires_at, bool)):
            return False
        current_time = int(time.time()) if now is None else int(now)
        return (
            claims.get("iss") == TOKEN_ISSUER
            and claims.get("aud") == TOKEN_AUDIENCE
            and claims.get("sub") == TOKEN_SUBJECT
            and claims.get("scope") == TOKEN_SCOPE
            and expires_at > current_time
            and issued_at <= current_time + 30
            and 0 < expires_at - issued_at <= ACCESS_TOKEN_TTL_SECONDS
        )
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return False
