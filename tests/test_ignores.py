from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.clients.invoiceninja import Expense
from app.clients.paperless import Document
from app.ignores import IgnoreStore
from app.matching.scorer import build_matches
from app.settings import Settings


def _expense(**kwargs) -> Expense:
    defaults = dict(
        id="abc",
        number="EX-001",
        amount=42.5,
        date=date(2026, 8, 1),
        vendor_name="Acme GmbH",
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
        title="Rechnung Acme",
        created_date=date(2026, 8, 2),
        added=date(2026, 8, 3),
        modified=None,
        correspondent_name="Acme GmbH",
        content="Gesamtbetrag 42,50 EUR",
        custom_fields={},
        raw={},
    )
    defaults.update(kwargs)
    return Document(**defaults)


def test_ignore_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "ignore.json"
    store = IgnoreStore(path)
    assert store.ignore_expense("EX-1") is True
    assert store.ignore_expense("EX-1") is False
    assert store.ignore_document(9) is True
    assert store.is_expense_ignored("EX-1")
    assert store.is_document_ignored(9)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["expenses"] == ["EX-1"]
    assert payload["documents"] == [9]

    again = IgnoreStore(path)
    assert again.expense_ids() == ["EX-1"]
    assert again.document_ids() == [9]


def test_unignore(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "ignore.json")
    store.ignore_expense("a")
    store.ignore_document(1)
    assert store.unignore_expense("a") is True
    assert store.unignore_expense("a") is False
    assert store.unignore_document(1) is True
    assert store.expense_ids() == []
    assert store.document_ids() == []


def test_corrupt_file_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "ignore.json"
    path.write_text("{not json", encoding="utf-8")
    store = IgnoreStore(path)
    assert store.expense_ids() == []
    store.ignore_expense("ok")
    assert json.loads(path.read_text())["expenses"] == ["ok"]


def test_drop_filters_lists(tmp_path: Path) -> None:
    store = IgnoreStore(tmp_path / "ignore.json")
    store.ignore_expense("hidden")
    store.ignore_document(2)
    expenses = [_expense(id="hidden"), _expense(id="open")]
    documents = [_doc(id=1), _doc(id=2)]
    assert [e.id for e in store.drop_expenses(expenses)] == ["open"]
    assert [d.id for d in store.drop_documents(documents)] == [1]


def test_ignored_items_excluded_from_matches(tmp_path: Path) -> None:
    settings = Settings(
        in_expense_field_invoice_number="custom_value1",
        in_expense_field_paperless_url="custom_value2",
        pl_field_invoice_number=1,
        pl_field_expense_number=2,
        pl_field_invoice_ninja_url=3,
        match_min_score=30,
    )
    store = IgnoreStore(tmp_path / "ignore.json")
    store.ignore_expense("skip")
    store.ignore_document(99)
    expenses = store.drop_expenses(
        [_expense(id="skip"), _expense(id="keep", custom_value1="RE-1")]
    )
    documents = store.drop_documents(
        [
            _doc(id=99, custom_fields={1: "RE-1"}),
            _doc(id=10, custom_fields={1: "RE-1"}),
        ]
    )
    matches = build_matches(expenses, documents, settings)
    assert [m.expense.id for m in matches] == ["keep"]
    assert matches[0].candidates
    assert all(c.document.id != 99 for c in matches[0].candidates)
