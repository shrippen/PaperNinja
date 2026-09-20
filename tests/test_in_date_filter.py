from __future__ import annotations

from datetime import date

import httpx

from app.clients.invoiceninja import (
    Expense,
    InvoiceNinjaClient,
    expense_list_params,
    expenses_leak_date_range,
)


def _expense(**kwargs) -> Expense:
    defaults = dict(
        id="1",
        number="EX-1",
        amount=1.0,
        date=date(2026, 6, 1),
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


def test_expense_list_params_include_date_range() -> None:
    params = expense_list_params(
        page=1,
        per_page=100,
        date_from=date(2026, 1, 1),
        date_to=date(2026, 12, 31),
    )
    assert params["include"] == "vendor"
    assert params["date"] == "2026-01-01,2026-12-31"


def test_undated_expenses_are_not_leaks() -> None:
    items = [
        _expense(date=date(2026, 1, 2)),
        _expense(id="2", date=None),
    ]
    assert expenses_leak_date_range(items, date(2026, 1, 1), date(2026, 12, 31)) is False
    assert expenses_leak_date_range(
        items + [_expense(id="3", date=date(2025, 12, 31))],
        date(2026, 1, 1),
        date(2026, 12, 31),
    ) is True


def _page(rows: list[dict], page: int = 1, total_pages: int = 1) -> dict:
    return {
        "data": rows,
        "meta": {"pagination": {"total_pages": total_pages, "current_page": page}},
    }


async def _client_with(handler) -> InvoiceNinjaClient:
    InvoiceNinjaClient.reset_date_filter_probe()
    client = InvoiceNinjaClient("https://in.example", "token")
    await client.aclose()
    client._client = httpx.AsyncClient(
        base_url="https://in.example",
        transport=httpx.MockTransport(handler),
    )
    return client


async def test_list_expenses_sends_date_filter() -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(
            200,
            json=_page(
                [{"id": "1", "number": "EX", "amount": 1, "date": "2026-06-01"}]
            ),
        )

    client = await _client_with(handler)
    try:
        expenses = await client.list_expenses(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 12, 31),
        )
    finally:
        await client.aclose()
    assert [e.id for e in expenses] == ["1"]
    assert seen[0].params["date"] == "2026-01-01,2026-12-31"
    assert InvoiceNinjaClient._date_filter_ok is True


async def test_list_expenses_disables_filter_on_400() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        date_param = request.url.params.get("date")
        calls.append(date_param or "")
        if date_param:
            return httpx.Response(400, text="invalid date")
        return httpx.Response(
            200,
            json=_page(
                [
                    {"id": "1", "date": "2025-01-01"},
                    {"id": "2", "date": "2026-06-01"},
                ]
            ),
        )

    client = await _client_with(handler)
    try:
        expenses = await client.list_expenses(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 12, 31),
        )
        again = await client.list_expenses(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 12, 31),
        )
    finally:
        await client.aclose()
    assert [e.id for e in expenses] == ["1", "2"]
    assert [e.id for e in again] == ["1", "2"]
    assert InvoiceNinjaClient._date_filter_ok is False
    assert calls[0] == "2026-01-01,2026-12-31"
    assert calls[1] == ""
    assert calls[2] == ""


async def test_list_expenses_disables_filter_when_api_ignores_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_page(
                [
                    {"id": "old", "date": "2024-03-01"},
                    {"id": "new", "date": "2026-03-01"},
                ]
            ),
        )

    client = await _client_with(handler)
    try:
        expenses = await client.list_expenses(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 12, 31),
        )
    finally:
        await client.aclose()
    assert {e.id for e in expenses} == {"old", "new"}
    assert InvoiceNinjaClient._date_filter_ok is False
