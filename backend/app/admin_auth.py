from __future__ import annotations

import json
import urllib.error
from typing import Any

from fastapi import HTTPException, Request

from .store import SupabaseStore

ADMIN_SESSION_COOKIE = "bs_admin_session"


def require_admin(request: Request, store: SupabaseStore) -> dict[str, Any]:
    token = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    if len(token) < 32:
        raise HTTPException(status_code=401, detail="admin_unauthorized")
    try:
        session = store.admin_action(token, "get_session")
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read())
        except (ValueError, OSError):
            error = {}
        message = str(error.get("message", "")).strip().lower()
        if message == "unauthorized":
            raise HTTPException(status_code=401, detail="admin_unauthorized")
        raise HTTPException(status_code=503, detail="admin_session_unavailable")
    except Exception:
        raise HTTPException(status_code=503, detail="admin_session_unavailable")
    if not isinstance(session, dict) or not session.get("email"):
        raise HTTPException(status_code=401, detail="admin_unauthorized")
    return {**session, "token": token}
