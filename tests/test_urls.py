from __future__ import annotations

from app.clients.invoiceninja import Expense
from app.settings import Settings
from app.urls import document_id_from_url, document_ids_from_url, paperless_url_of


def test_document_id_from_url() -> None:
    assert document_id_from_url("https://pl.example/documents/123/") == 123
    assert document_id_from_url("") is None
    assert document_ids_from_url(
        "https://pl/documents/1/ https://pl/documents/2/"
    ) == [1, 2]


def test_paperless_url_of() -> None:
    settings = Settings(in_expense_field_paperless_url="custom_value2")
    expense = Expense(
        id="1",
        number="E1",
        amount=1.0,
        date=None,
        vendor_name="",
        public_notes="",
        private_notes="",
        custom_value1="",
        custom_value2="https://pl/documents/5/",
        custom_value3="",
        custom_value4="",
        updated_at=None,
        raw={},
    )
    assert paperless_url_of(expense, settings) == "https://pl/documents/5/"
