from __future__ import annotations

from dataclasses import dataclass

from app.clients.invoiceninja import Expense, InvoiceNinjaClient
from app.clients.paperless import Document, PaperlessClient
from app.settings import Settings
from app.urls import document_ids_from_url, paperless_url_of


@dataclass(slots=True)
class LinkResult:
    expense_id: str
    document_id: int
    paperless_url: str
    invoice_ninja_url: str
    expense_number: str
    invoice_number: str
    document_title: str
    vendor_name: str
    correspondent_name: str


async def link_expense_document(
    *,
    settings: Settings,
    in_client: InvoiceNinjaClient,
    pl_client: PaperlessClient,
    expense: Expense,
    document: Document,
) -> LinkResult:
    if not settings.mapping_complete:
        raise ValueError("Field mapping incomplete — configure ENV via Setup")

    inv_slot = settings.in_expense_field_invoice_number
    url_slot = settings.in_expense_field_paperless_url
    assert inv_slot and url_slot
    assert settings.pl_field_expense_number is not None
    assert settings.pl_field_invoice_ninja_url is not None
    assert settings.pl_field_invoice_number is not None

    paperless_url = settings.document_url(document.id)
    invoice_ninja_url = settings.expense_url(expense.id)
    expense_number = expense.number or expense.id

    invoice_number = (expense.custom(inv_slot) or "").strip()
    if not invoice_number:
        # Prefer Paperless invoice number if expense slot empty
        pl_inv = document.custom_value(settings.pl_field_invoice_number)
        invoice_number = str(pl_inv or "").strip()

    in_updates = {
        url_slot: paperless_url,
    }
    if invoice_number and not (expense.custom(inv_slot) or "").strip():
        in_updates[inv_slot] = invoice_number
    elif invoice_number:
        # Keep / refresh invoice number on expense
        in_updates[inv_slot] = invoice_number

    await in_client.update_expense_custom_fields(expense.id, in_updates)

    pl_updates: dict[int, object] = {
        settings.pl_field_expense_number: expense_number,
        settings.pl_field_invoice_ninja_url: invoice_ninja_url,
    }
    if invoice_number:
        pl_updates[settings.pl_field_invoice_number] = invoice_number

    try:
        await pl_client.modify_custom_fields(document.id, pl_updates)
    except Exception:
        # Best-effort rollback of IN URL to avoid one-sided link
        try:
            await in_client.update_expense_custom_fields(
                expense.id,
                {url_slot: ""},
            )
        except Exception:
            pass
        raise

    return LinkResult(
        expense_id=expense.id,
        document_id=document.id,
        paperless_url=paperless_url,
        invoice_ninja_url=invoice_ninja_url,
        expense_number=expense_number,
        invoice_number=invoice_number,
        document_title=document.title,
        vendor_name=expense.vendor_name,
        correspondent_name=document.correspondent_name,
    )


async def link_expense_documents(
    *,
    settings: Settings,
    in_client: InvoiceNinjaClient,
    pl_client: PaperlessClient,
    expense: Expense,
    documents: list[Document],
) -> list[LinkResult]:
    if not documents:
        raise ValueError("Keine Dokumente")
    if len(documents) == 1:
        return [
            await link_expense_document(
                settings=settings,
                in_client=in_client,
                pl_client=pl_client,
                expense=expense,
                document=documents[0],
            )
        ]
    if not settings.mapping_complete:
        raise ValueError("Field mapping incomplete — configure ENV via Setup")
    url_slot = settings.in_expense_field_paperless_url
    assert url_slot
    assert settings.pl_field_expense_number is not None
    assert settings.pl_field_invoice_ninja_url is not None

    invoice_ninja_url = settings.expense_url(expense.id)
    expense_number = expense.number or expense.id
    urls = [settings.document_url(document.id) for document in documents]
    joined = " ".join(urls)
    await in_client.update_expense_custom_fields(expense.id, {url_slot: joined})

    results: list[LinkResult] = []
    linked: list[Document] = []
    try:
        for document in documents:
            await pl_client.modify_custom_fields(
                document.id,
                {
                    settings.pl_field_expense_number: expense_number,
                    settings.pl_field_invoice_ninja_url: invoice_ninja_url,
                },
            )
            linked.append(document)
            results.append(
                LinkResult(
                    expense_id=expense.id,
                    document_id=document.id,
                    paperless_url=settings.document_url(document.id),
                    invoice_ninja_url=invoice_ninja_url,
                    expense_number=expense_number,
                    invoice_number="",
                    document_title=document.title,
                    vendor_name=expense.vendor_name,
                    correspondent_name=document.correspondent_name,
                )
            )
    except Exception:
        try:
            await in_client.update_expense_custom_fields(expense.id, {url_slot: ""})
        except Exception:
            pass
        for document in linked:
            try:
                await pl_client.modify_custom_fields(
                    document.id,
                    {
                        settings.pl_field_expense_number: "",
                        settings.pl_field_invoice_ninja_url: "",
                    },
                )
            except Exception:
                pass
        raise
    return results


async def unlink_expense_document(
    *,
    settings: Settings,
    in_client: InvoiceNinjaClient,
    pl_client: PaperlessClient,
    expense: Expense,
    document: Document,
) -> None:
    if not settings.mapping_complete:
        raise ValueError("Field mapping incomplete — configure ENV via Setup")
    url_slot = settings.in_expense_field_paperless_url
    assert url_slot
    assert settings.pl_field_expense_number is not None
    assert settings.pl_field_invoice_ninja_url is not None

    remaining = [
        doc_id
        for doc_id in document_ids_from_url(paperless_url_of(expense, settings))
        if doc_id != document.id
    ]
    remaining_urls = " ".join(settings.document_url(doc_id) for doc_id in remaining)
    await in_client.update_expense_custom_fields(expense.id, {url_slot: remaining_urls})
    await pl_client.modify_custom_fields(
        document.id,
        {
            settings.pl_field_expense_number: "",
            settings.pl_field_invoice_ninja_url: "",
        },
    )
