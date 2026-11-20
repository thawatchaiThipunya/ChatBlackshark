from __future__ import annotations

from typing import Any


def recent_context(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return five customer turns and their replies, plus the last two product scopes."""
    groups: list[list[dict[str, Any]]] = []
    for row in messages:
        if row.get("role") == "user":
            groups.append([row])
        elif row.get("role") == "assistant" and groups:
            groups[-1].append(row)
    selected = groups[-5:]
    history: list[dict[str, Any]] = []
    for group in selected:
        for row in group[:2]:
            payload = row.get("payload") or {}
            turn = {"role": row["role"], "content": row.get("content") or "",
                    "product_name": payload.get("product_name"),
                    "card_title": payload.get("card_title")}
            if row["role"] == "user":
                descriptions = payload.get("vision_descriptions") or []
                turn["vision_descriptions"] = [str(value) for value in descriptions if value]
            history.append(turn)
    scopes: list[dict[str, Any]] = []
    for group in groups[-2:]:
        user = group[0]
        reply = group[1] if len(group) > 1 else {}
        analysis = (reply.get("payload") or {}).get("intent_analysis") or {}
        scope = {"question": user.get("content") or ""}
        if analysis.get("list_all") and analysis.get("category"):
            scope["category"] = analysis["category"]
        else:
            names = analysis.get("product_names") or []
            scope["product_names"] = [str(name) for name in names[:3]]
        scopes.append(scope)
    return history, scopes
