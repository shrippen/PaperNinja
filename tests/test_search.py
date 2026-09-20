from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

from app.clients.invoiceninja import Expense
from app.clients.paperless import CustomFieldDef
from app.search import PRESET_KEYS, search_documents_for_expense
from app.settings import Settings


def test_preset_keys_have_translations() -> None:
    from app.i18n import TRANSLATIONS

    for lang in TRANSLATIONS:
        for key in PRESET_KEYS:
            assert TRANSLATIONS[lang][f"preset_{key}"]


async def test_search_around_date_window() -> None:
    settings = Settings(pl_field_expense_number=2)
    expense = Expense(
        id="1",
        number="EX",
        amount=10.0,
        date=date(2026, 6, 15),
        vendor_name="Acme",
        public_notes="",
        private_notes="",
        custom_value1="",
        custom_value2="",
        custom_value3="",
        custom_value4="",
        updated_at=None,
        raw={},
    )
    client = AsyncMock()
    client.search_documents = AsyncMock(return_value=[])
    await search_documents_for_expense(
        pl_client=client,
        settings=settings,
        expense=expense,
        year=2026,
        preset="around_date",
    )
    kwargs = client.search_documents.await_args.kwargs
    assert kwargs["created_gte"] == date(2026, 6, 1)
    assert kwargs["created_lte"] == date(2026, 6, 29)


async def test_search_unlinked_uses_custom_field_query() -> None:
    settings = Settings(pl_field_expense_number=2)
    expense = Expense(
        id="1",
        number="EX",
        amount=10.0,
        date=date(2026, 1, 1),
        vendor_name="",
        public_notes="",
        private_notes="",
        custom_value1="",
        custom_value2="",
        custom_value3="",
        custom_value4="",
        updated_at=None,
        raw={},
    )
    client = AsyncMock()
    client.list_custom_fields = AsyncMock(
        return_value=[
            CustomFieldDef(id=2, name="Expense no", data_type="text", env_candidates=[])
        ]
    )
    client.search_documents = AsyncMock(return_value=[])
    await search_documents_for_expense(
        pl_client=client,
        settings=settings,
        expense=expense,
        year=2026,
        preset="unlinked",
    )
    query = client.search_documents.await_args.kwargs["custom_field_query"]
    assert query[0] == "OR"
