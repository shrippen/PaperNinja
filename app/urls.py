from __future__ import annotations

import re

from app.clients.invoiceninja import Expense
from app.settings import Settings

_DOC_ID_RE = re.compile(r"/documents/(\d+)")


def document_ids_from_url(url: str) -> list[int]:
    return [int(match) for match in _DOC_ID_RE.findall(url or "")]


def document_id_from_url(url: str) -> int | None:
    ids = document_ids_from_url(url)
    return ids[0] if ids else None


def paperless_url_of(expense: Expense, settings: Settings) -> str:
    slot = settings.in_expense_field_paperless_url
    if not slot:
        return ""
    return (expense.custom(slot) or "").strip()
