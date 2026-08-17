from __future__ import annotations

from app.session_links import forget_link, remember_link, session_links


def test_remember_and_forget() -> None:
    session: dict = {}
    remember_link(session, {"expense_id": "1", "document_id": 10, "linked_at": "now"})
    remember_link(session, {"expense_id": "2", "document_id": 20, "linked_at": "later"})
    assert len(session_links(session)) == 2
    assert session_links(session)[0]["expense_id"] == "2"

    forget_link(session, "2", 20)
    assert len(session_links(session)) == 1
    assert session_links(session)[0]["expense_id"] == "1"
