from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.clients.invoiceninja import Expense, InvoiceNinjaClient
from app.clients.paperless import Document, PaperlessClient
from app.session_links import session_links
from app.settings import Settings
from app.urls import document_ids_from_url, paperless_url_of


@dataclass(slots=True)
class LinkedPair:
    expense: Expense
    document: Document | None
    linked_at: str
    source: str


async def list_linked_pairs(
    *,
    session: dict[str, Any],
    settings: Settings,
    in_client: InvoiceNinjaClient,
    pl_client: PaperlessClient,
    limit: int = 40,
) -> tuple[list[LinkedPair], str]:
    session_items = session_links(session)
    if session_items:
        out: list[LinkedPair] = []
        for item in session_items[:limit]:
            expense = await in_client.get_expense(str(item["expense_id"]))
            doc_id = int(item["document_id"])
            try:
                document = await pl_client.get_document(doc_id)
            except Exception:
                document = None
            out.append(
                LinkedPair(
                    expense=expense,
                    document=document,
                    linked_at=str(item.get("linked_at") or ""),
                    source="session",
                )
            )
        return out, "session"

    expenses = await in_client.list_expenses()
    linked: list[tuple[Expense, int]] = []
    for expense in expenses:
        url = paperless_url_of(expense, settings)
        for doc_id in document_ids_from_url(url):
            linked.append((expense, doc_id))

    def sort_key(pair: tuple[Expense, int]) -> datetime:
        expense, _ = pair
        return expense.updated_at or datetime.min.replace(tzinfo=None)

    linked.sort(key=sort_key, reverse=True)
    out = []
    for expense, doc_id in linked[:limit]:
        try:
            document = await pl_client.get_document(doc_id)
        except Exception:
            document = None
        ts = ""
        if expense.updated_at:
            ts = expense.updated_at.isoformat(timespec="seconds")
        elif document and document.modified:
            ts = document.modified.isoformat(timespec="seconds")
        out.append(
            LinkedPair(
                expense=expense,
                document=document,
                linked_at=ts,
                source="api",
            )
        )
    return out, "api"
