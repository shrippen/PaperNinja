from __future__ import annotations

from datetime import date
from pathlib import Path

from app.alias_backfill import backfill_vendor_aliases
from app.aliases import VendorAliasStore
from app.clients.invoiceninja import Expense
from app.clients.paperless import Document
from app.settings import Settings


def _expense() -> Expense:
    return Expense(
        id="1",
        number="EX-1",
        amount=10.0,
        date=date(2026, 1, 1),
        vendor_name="Deutsche Bahn",
        public_notes="",
        private_notes="",
        custom_value1="",
        custom_value2="https://pl.example/documents/99/",
        custom_value3="",
        custom_value4="",
        updated_at=None,
        raw={},
    )


def _doc() -> Document:
    return Document(
        id=99,
        title="Ticket",
        created_date=date(2026, 1, 1),
        added=None,
        modified=None,
        correspondent_name="DB AG",
        content="",
        custom_fields={},
        raw={},
    )


class _FakeIN:
    def __init__(self, expenses: list[Expense]) -> None:
        self.expenses = expenses

    async def list_expenses(self) -> list[Expense]:
        return self.expenses


class _FakePL:
    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents

    async def get_documents_by_ids(self, ids: list[int]) -> list[Document]:
        wanted = set(ids)
        return [doc for doc in self.documents if doc.id in wanted]


async def test_backfill_from_existing_links(tmp_path: Path) -> None:
    settings = Settings(in_expense_field_paperless_url="custom_value2")
    store = VendorAliasStore(tmp_path / "vendor_aliases.json")
    learned = await backfill_vendor_aliases(
        settings=settings,
        in_client=_FakeIN([_expense()]),
        pl_client=_FakePL([_doc()]),
        aliases=store,
    )
    assert learned == 1
    assert "db ag" in store.equivalents("Deutsche Bahn")
    assert store.backfilled is True
    again = await backfill_vendor_aliases(
        settings=settings,
        in_client=_FakeIN([_expense()]),
        pl_client=_FakePL([_doc()]),
        aliases=store,
    )
    assert again == 0
