from __future__ import annotations

from datetime import date

from app.clients.invoiceninja import Expense
from app.clients.paperless import Document
from app.matching.scorer import (
    build_matches,
    combo_amount,
    filter_expenses_by_year,
    filter_unlinked_expenses,
    parse_amount,
    score_pair,
)
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
        content="Gesamtbetrag 42,50 EUR Rechnungsnr RE-99",
        custom_fields={},
        raw={},
    )
    defaults.update(kwargs)
    return Document(**defaults)


def test_parse_amount_german():
    assert parse_amount("42,50") == 42.5
    assert parse_amount("1.234,56") == 1234.56
    assert parse_amount("EUR 42.50") == 42.5


def test_score_pair_strong_match():
    settings = Settings(
        in_expense_field_invoice_number="custom_value1",
        in_expense_field_paperless_url="custom_value2",
        pl_field_invoice_number=1,
        pl_field_expense_number=2,
        pl_field_invoice_ninja_url=3,
        match_min_score=40,
        match_date_window_days=7,
        match_amount_tolerance=0.02,
    )
    expense = _expense(custom_value1="RE-99")
    document = _doc(custom_fields={1: "RE-99"})
    candidate = score_pair(expense, document, settings)
    assert candidate.score >= 70
    assert any("Betrag" in r for r in candidate.reasons)
    assert len(candidate.factors) == 4
    assert all(f.detail for f in candidate.factors)
    assert sum(f.points for f in candidate.factors) >= 70


def test_filter_unlinked_expenses():
    settings = Settings(in_expense_field_paperless_url="custom_value2")
    linked = _expense(id="1", custom_value2="https://paperless/documents/1/")
    open_ = _expense(id="2", custom_value2="")
    result = filter_unlinked_expenses([linked, open_], settings)
    assert [e.id for e in result] == ["2"]


def test_filter_expenses_by_year():
    items = [
        _expense(id="1", date=date(2026, 1, 1)),
        _expense(id="2", date=date(2025, 12, 31)),
        _expense(id="3", date=None),
    ]
    assert [e.id for e in filter_expenses_by_year(items, 2026)] == ["1"]


def test_build_matches_orders_by_score():
    settings = Settings(
        in_expense_field_invoice_number="custom_value1",
        in_expense_field_paperless_url="custom_value2",
        pl_field_invoice_number=1,
        pl_field_expense_number=2,
        pl_field_invoice_ninja_url=3,
        match_min_score=30,
        match_date_window_days=7,
        match_top_n=3,
    )
    expense = _expense()
    weak = _doc(id=1, title="Sonstiges", correspondent_name="Other", content="1,00")
    strong = _doc(id=2)
    matches = build_matches([expense], [weak, strong], settings)
    assert matches[0].candidates
    assert matches[0].candidates[0].document.id == 2
    assert matches[0].combos == []


def _combo_settings(**kwargs) -> Settings:
    values = dict(
        in_expense_field_invoice_number="custom_value1",
        in_expense_field_paperless_url="custom_value2",
        pl_field_invoice_number=1,
        pl_field_expense_number=2,
        pl_field_invoice_ninja_url=3,
        pl_field_amount=9,
        match_min_score=40,
        match_date_window_days=7,
        match_amount_tolerance=0.02,
        match_top_n=3,
    )
    values.update(kwargs)
    return Settings(**values)


def test_combo_amount_prefers_field():
    settings = _combo_settings()
    document = _doc(custom_fields={9: "12,50"}, content="99,00 EUR")
    assert combo_amount(document, settings) == 12.5


def test_combo_amount_skips_ambiguous_ocr():
    settings = _combo_settings(pl_field_amount=None)
    document = _doc(content="Zwischensumme 10,00 Gesamt 42,50")
    assert combo_amount(document, settings) is None


def test_build_matches_combo_sums_two_documents():
    settings = _combo_settings()
    expense = _expense(amount=42.5)
    first = _doc(id=1, title="Teil A", custom_fields={9: 20.0}, content="")
    second = _doc(id=2, title="Teil B", custom_fields={9: 22.5}, content="")
    other = _doc(id=3, title="Unrelated", custom_fields={9: 5.0}, content="")
    matches = build_matches(
        [expense],
        [first, second, other],
        settings,
        include_combos=True,
    )
    assert matches[0].combos
    combo = matches[0].combos[0]
    assert {doc.id for doc in combo.documents} == {1, 2}
    assert combo.sum_amount == 42.5
    assert combo.score >= 40


def test_build_matches_combo_off_by_default():
    settings = _combo_settings()
    expense = _expense(amount=42.5)
    first = _doc(id=1, custom_fields={9: 20.0}, content="")
    second = _doc(id=2, custom_fields={9: 22.5}, content="")
    matches = build_matches([expense], [first, second], settings)
    assert matches[0].combos == []

