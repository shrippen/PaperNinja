from __future__ import annotations

from datetime import date

import pytest

from app.clients.invoiceninja import Expense
from app.clients.paperless import Document
from app.linking import (
    link_expense_document,
    link_expense_documents,
    unlink_expense_document,
)
from app.settings import Settings


def _settings() -> Settings:
    return Settings(
        invoice_ninja_url="https://in.example",
        invoice_ninja_token="t",
        paperless_url="https://pl.example",
        paperless_token="t",
        in_expense_field_invoice_number="custom_value1",
        in_expense_field_paperless_url="custom_value2",
        pl_field_invoice_number=1,
        pl_field_expense_number=2,
        pl_field_invoice_ninja_url=3,
    )


def _expense(**kwargs) -> Expense:
    defaults = dict(
        id="exp-1",
        number="EX-001",
        amount=10.0,
        date=date(2026, 8, 1),
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
    defaults.update(kwargs)
    return Expense(**defaults)


def _doc(**kwargs) -> Document:
    defaults = dict(
        id=10,
        title="Beleg",
        created_date=date(2026, 8, 1),
        added=date(2026, 8, 1),
        modified=None,
        correspondent_name="Acme GmbH",
        content="",
        custom_fields={},
        raw={},
    )
    defaults.update(kwargs)
    return Document(**defaults)


class FakeIN:
    def __init__(self, expense: Expense) -> None:
        self.expense = expense
        self.updates: list[tuple[str, dict[str, str]]] = []

    async def update_expense_custom_fields(
        self, expense_id: str, updates: dict[str, str]
    ) -> Expense:
        self.updates.append((expense_id, dict(updates)))
        for key, value in updates.items():
            setattr(self.expense, key, value)
        return self.expense


class FakePL:
    def __init__(self, fail_on: set[int] | None = None) -> None:
        self.updates: list[tuple[int, dict]] = []
        self.fail_on = fail_on or set()

    async def modify_custom_fields(self, document_id: int, add_fields: dict) -> None:
        if document_id in self.fail_on:
            raise RuntimeError("paperless down")
        self.updates.append((document_id, dict(add_fields)))


async def test_link_one_writes_both_sides() -> None:
    settings = _settings()
    expense = _expense(custom_value1="RE-9")
    document = _doc(id=44)
    inn = FakeIN(expense)
    pl = FakePL()
    result = await link_expense_document(
        settings=settings,
        in_client=inn,  # type: ignore[arg-type]
        pl_client=pl,  # type: ignore[arg-type]
        expense=expense,
        document=document,
    )
    assert result.expense_id == "exp-1"
    assert result.document_id == 44
    assert inn.updates[0][1]["custom_value2"] == "https://pl.example/documents/44/"
    assert inn.updates[0][1]["custom_value1"] == "RE-9"
    assert pl.updates[0][0] == 44
    assert pl.updates[0][1][2] == "EX-001"
    assert pl.updates[0][1][3] == "https://in.example/expenses/exp-1/edit"
    assert pl.updates[0][1][1] == "RE-9"


async def test_link_copies_invoice_number_from_paperless() -> None:
    settings = _settings()
    expense = _expense()
    document = _doc(custom_fields={1: "INV-77"})
    inn = FakeIN(expense)
    pl = FakePL()
    result = await link_expense_document(
        settings=settings,
        in_client=inn,  # type: ignore[arg-type]
        pl_client=pl,  # type: ignore[arg-type]
        expense=expense,
        document=document,
    )
    assert result.invoice_number == "INV-77"
    assert inn.updates[0][1]["custom_value1"] == "INV-77"


async def test_link_rolls_back_in_when_paperless_fails() -> None:
    settings = _settings()
    expense = _expense()
    inn = FakeIN(expense)
    pl = FakePL(fail_on={10})
    with pytest.raises(RuntimeError, match="paperless down"):
        await link_expense_document(
            settings=settings,
            in_client=inn,  # type: ignore[arg-type]
            pl_client=pl,  # type: ignore[arg-type]
            expense=expense,
            document=_doc(),
        )
    assert inn.updates[0][1]["custom_value2"].endswith("/documents/10/")
    assert inn.updates[1][1]["custom_value2"] == ""


async def test_link_many_joins_urls_and_rolls_back() -> None:
    settings = _settings()
    expense = _expense()
    inn = FakeIN(expense)
    pl = FakePL(fail_on={12})
    with pytest.raises(RuntimeError):
        await link_expense_documents(
            settings=settings,
            in_client=inn,  # type: ignore[arg-type]
            pl_client=pl,  # type: ignore[arg-type]
            expense=expense,
            documents=[_doc(id=11), _doc(id=12)],
        )
    joined = inn.updates[0][1]["custom_value2"]
    assert "/documents/11/" in joined
    assert "/documents/12/" in joined
    assert inn.updates[-1][1]["custom_value2"] == ""
    cleared = [item for item in pl.updates if item[0] == 11 and item[1][2] == ""]
    assert cleared


async def test_link_many_success() -> None:
    settings = _settings()
    expense = _expense()
    inn = FakeIN(expense)
    pl = FakePL()
    results = await link_expense_documents(
        settings=settings,
        in_client=inn,  # type: ignore[arg-type]
        pl_client=pl,  # type: ignore[arg-type]
        expense=expense,
        documents=[_doc(id=11), _doc(id=12)],
    )
    assert [r.document_id for r in results] == [11, 12]
    urls = inn.updates[0][1]["custom_value2"]
    assert urls.split() == [
        "https://pl.example/documents/11/",
        "https://pl.example/documents/12/",
    ]
    assert {u[0] for u in pl.updates} == {11, 12}


async def test_unlink_keeps_remaining_urls() -> None:
    settings = _settings()
    expense = _expense(
        custom_value2=(
            "https://pl.example/documents/11/ https://pl.example/documents/12/"
        )
    )
    inn = FakeIN(expense)
    pl = FakePL()
    await unlink_expense_document(
        settings=settings,
        in_client=inn,  # type: ignore[arg-type]
        pl_client=pl,  # type: ignore[arg-type]
        expense=expense,
        document=_doc(id=11),
    )
    assert inn.updates[0][1]["custom_value2"] == "https://pl.example/documents/12/"
    assert pl.updates[0][1][2] == ""
    assert pl.updates[0][1][3] == ""


async def test_link_rejects_incomplete_mapping() -> None:
    settings = Settings(
        _env_file=None,
        in_expense_field_invoice_number=None,
        in_expense_field_paperless_url=None,
        pl_field_invoice_number=None,
        pl_field_expense_number=None,
        pl_field_invoice_ninja_url=None,
    )
    with pytest.raises(ValueError, match="mapping"):
        await link_expense_document(
            settings=settings,
            in_client=FakeIN(_expense()),  # type: ignore[arg-type]
            pl_client=FakePL(),  # type: ignore[arg-type]
            expense=_expense(),
            document=_doc(),
        )


async def test_link_many_rejects_empty() -> None:
    with pytest.raises(ValueError):
        await link_expense_documents(
            settings=_settings(),
            in_client=FakeIN(_expense()),  # type: ignore[arg-type]
            pl_client=FakePL(),  # type: ignore[arg-type]
            expense=_expense(),
            documents=[],
        )
