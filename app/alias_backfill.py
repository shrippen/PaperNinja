from __future__ import annotations

from app.aliases import VendorAliasStore
from app.clients.invoiceninja import Expense, InvoiceNinjaClient
from app.clients.paperless import PaperlessClient
from app.settings import Settings
from app.urls import document_ids_from_url, paperless_url_of


async def backfill_vendor_aliases(
    *,
    settings: Settings,
    in_client: InvoiceNinjaClient,
    pl_client: PaperlessClient,
    aliases: VendorAliasStore,
    expenses: list[Expense] | None = None,
) -> int:
    """Learn vendor/correspondent aliases from expenses that already have a Paperless URL."""
    if aliases.backfilled:
        return 0
    if expenses is None:
        expenses = await in_client.list_expenses()
    pairs: list[tuple[Expense, int]] = []
    for expense in expenses:
        if not (expense.vendor_name or "").strip():
            continue
        for doc_id in document_ids_from_url(paperless_url_of(expense, settings)):
            pairs.append((expense, doc_id))
    ids = list(dict.fromkeys(doc_id for _, doc_id in pairs))
    by_id = {doc.id: doc for doc in await pl_client.get_documents_by_ids(ids)}
    learned = 0
    for expense, doc_id in pairs:
        document = by_id.get(doc_id)
        if document is None or not (document.correspondent_name or "").strip():
            continue
        if aliases.learn(expense.vendor_name, document.correspondent_name, persist=False):
            learned += 1
    aliases.mark_backfilled()
    return learned
