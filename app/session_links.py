from __future__ import annotations

from typing import Any

SESSION_LINKS_KEY = "recent_links"
MAX_SESSION_LINKS = 40


def remember_link(session: dict[str, Any], item: dict[str, Any]) -> None:
    items = list(session.get(SESSION_LINKS_KEY) or [])
    key = (str(item.get("expense_id")), str(item.get("document_id")))
    items = [x for x in items if (str(x.get("expense_id")), str(x.get("document_id"))) != key]
    items.insert(0, item)
    session[SESSION_LINKS_KEY] = items[:MAX_SESSION_LINKS]


def forget_link(session: dict[str, Any], expense_id: str, document_id: int) -> None:
    items = list(session.get(SESSION_LINKS_KEY) or [])
    session[SESSION_LINKS_KEY] = [
        x
        for x in items
        if not (
            str(x.get("expense_id")) == str(expense_id)
            and str(x.get("document_id")) == str(document_id)
        )
    ]


def session_links(session: dict[str, Any]) -> list[dict[str, Any]]:
    raw = session.get(SESSION_LINKS_KEY) or []
    return [x for x in raw if isinstance(x, dict)]
