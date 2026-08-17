from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.clients.invoiceninja import Expense
from app.clients.paperless import Document, PaperlessClient
from app.settings import Settings


PRESETS: list[tuple[str, str]] = [
    ("around_date", "Datum ±14d"),
    ("amount", "Betrag ±5%"),
    ("vendor", "Vendor/Korrespondent"),
    ("invoice", "Rechnungsnr."),
    ("year", "Jahr"),
    ("unlinked", "Nur unlinked"),
]


async def search_documents_for_expense(
    *,
    pl_client: PaperlessClient,
    settings: Settings,
    expense: Expense,
    year: int,
    q: str = "",
    preset: str = "",
    unlinked_only: bool = False,
    date_from: str = "",
    date_to: str = "",
    correspondent: str = "",
) -> list[Document]:
    created_gte = date(year, 1, 1)
    created_lte = date(year, 12, 31)
    tags_id_in: list[int] | None = None
    query: str | None = None
    title_content: str | None = None
    corr = correspondent.strip()
    custom_field_query: list | None = None

    if preset == "around_date" and expense.date:
        created_gte = expense.date - timedelta(days=14)
        created_lte = expense.date + timedelta(days=14)
    elif preset == "year":
        pass
    elif preset == "amount" and expense.amount:
        query = f"{expense.amount:.2f}"
        title_content = query.replace(".", ",")
    elif preset == "vendor" and expense.vendor_name:
        corr = expense.vendor_name
        title_content = expense.vendor_name
    elif preset == "invoice":
        inv = ""
        slot = settings.in_expense_field_invoice_number
        if slot:
            inv = expense.custom(slot)
        if not inv:
            inv = expense.number
        if inv:
            query = inv
    elif preset == "unlinked":
        unlinked_only = True

    if date_from:
        created_gte = date.fromisoformat(date_from)
    if date_to:
        created_lte = date.fromisoformat(date_to)

    if q.strip():
        query = q.strip()

    if unlinked_only and settings.pl_field_expense_number is not None:
        field_name = await _resolve_field_name(pl_client, settings.pl_field_expense_number)
        empty_q = ["OR", [[field_name, "isnull", True], [field_name, "exact", ""]]]
        if custom_field_query:
            custom_field_query = ["AND", [custom_field_query, empty_q]]
        else:
            custom_field_query = empty_q

    docs = await pl_client.search_documents(
        query=query,
        title_content=title_content,
        created_gte=created_gte,
        created_lte=created_lte,
        tags_id_in=tags_id_in,
        correspondent_name=corr or None,
        custom_field_query=custom_field_query,
        page_size=25,
        max_pages=2,
    )
    return docs


async def _resolve_field_name(pl_client: PaperlessClient, field_id: int) -> str:
    fields = await pl_client.list_custom_fields()
    for field in fields:
        if field.id == field_id:
            return field.name
    return str(field_id)
