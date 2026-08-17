from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx


class InvoiceNinjaError(Exception):
    pass


@dataclass(slots=True)
class CustomFieldSlot:
    slot: str  # custom_value1 … custom_value4
    label: str
    env_candidates: list[str]


@dataclass(slots=True)
class Expense:
    id: str
    number: str
    amount: float
    date: date | None
    vendor_name: str
    public_notes: str
    private_notes: str
    custom_value1: str
    custom_value2: str
    custom_value3: str
    custom_value4: str
    updated_at: datetime | None
    raw: dict[str, Any]

    def custom(self, slot: str) -> str:
        return str(getattr(self, slot, "") or "")


class InvoiceNinjaClient:
    def __init__(self, base_url: str, token: str, timeout: float = 60.0) -> None:
        if not base_url or not token:
            raise InvoiceNinjaError("Invoice Ninja URL and token are required")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-API-TOKEN": token,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def ping(self) -> dict[str, Any]:
        response = await self._client.get("/api/v1/ping")
        self._raise_for_status(response)
        return response.json()

    async def list_expense_custom_fields(self) -> list[CustomFieldSlot]:
        """Resolve labels for expense custom_value1–4 from company settings."""
        labels = await self._expense_custom_labels()
        env_map = {
            "custom_value1": [
                "IN_EXPENSE_FIELD_INVOICE_NUMBER=custom_value1",
                "IN_EXPENSE_FIELD_PAPERLESS_URL=custom_value1",
            ],
            "custom_value2": [
                "IN_EXPENSE_FIELD_INVOICE_NUMBER=custom_value2",
                "IN_EXPENSE_FIELD_PAPERLESS_URL=custom_value2",
            ],
            "custom_value3": [
                "IN_EXPENSE_FIELD_INVOICE_NUMBER=custom_value3",
                "IN_EXPENSE_FIELD_PAPERLESS_URL=custom_value3",
            ],
            "custom_value4": [
                "IN_EXPENSE_FIELD_INVOICE_NUMBER=custom_value4",
                "IN_EXPENSE_FIELD_PAPERLESS_URL=custom_value4",
            ],
        }
        fields: list[CustomFieldSlot] = []
        for i in range(1, 5):
            slot = f"custom_value{i}"
            label = labels.get(slot) or labels.get(str(i)) or f"(unnamed slot {i})"
            fields.append(
                CustomFieldSlot(
                    slot=slot,
                    label=label,
                    env_candidates=env_map[slot],
                )
            )
        return fields

    async def _expense_custom_labels(self) -> dict[str, str]:
        """Best-effort extraction across Invoice Ninja company payloads."""
        company = await self._fetch_company()
        labels: dict[str, str] = {}

        custom_fields = company.get("custom_fields") or {}
        if isinstance(custom_fields, dict):
            for key, value in custom_fields.items():
                key_l = str(key).lower()
                if not key_l.startswith("expense"):
                    continue
                # expense1 / Expense1 / expense_custom_value1
                digit = "".join(ch for ch in key_l if ch.isdigit())
                if digit in {"1", "2", "3", "4"}:
                    labels[f"custom_value{digit}"] = str(value or "").strip() or labels.get(
                        f"custom_value{digit}", ""
                    )

        settings = company.get("settings") or {}
        if isinstance(settings, dict):
            for i in range(1, 5):
                for candidate in (
                    f"expense_custom_value{i}",
                    f"custom_value{i}",
                    f"expense{i}",
                ):
                    raw = settings.get(candidate)
                    if isinstance(raw, str) and raw.strip():
                        labels[f"custom_value{i}"] = raw.strip()

            # Sometimes nested: settings.expense_custom_fields = ["a","b","c","d"]
            nested = settings.get("expense_custom_fields")
            if isinstance(nested, list):
                for idx, raw in enumerate(nested[:4], start=1):
                    if isinstance(raw, str) and raw.strip():
                        labels[f"custom_value{idx}"] = raw.strip()
            elif isinstance(nested, dict):
                for i in range(1, 5):
                    raw = nested.get(str(i)) or nested.get(f"custom_value{i}")
                    if isinstance(raw, str) and raw.strip():
                        labels[f"custom_value{i}"] = raw.strip()

        return labels

    async def _fetch_company(self) -> dict[str, Any]:
        # Prefer companies endpoint; fall back to company bootstrap shapes.
        for path in ("/api/v1/companies", "/api/v1/company"):
            response = await self._client.get(path)
            if response.status_code == 404:
                continue
            self._raise_for_status(response)
            payload = response.json()
            data = payload.get("data", payload)
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                # nested company
                if "company" in data and isinstance(data["company"], dict):
                    return data["company"]
                return data
        raise InvoiceNinjaError("Could not load company settings for custom fields")

    async def list_expenses(self, *, per_page: int = 100) -> list[Expense]:
        expenses: list[Expense] = []
        page = 1
        while True:
            response = await self._client.get(
                "/api/v1/expenses",
                params={
                    "per_page": per_page,
                    "page": page,
                    "include": "vendor",
                },
            )
            self._raise_for_status(response)
            payload = response.json()
            rows = payload.get("data") or []
            for row in rows:
                expenses.append(self._parse_expense(row))
            meta = payload.get("meta", {}).get("pagination") or payload.get("meta") or {}
            total_pages = int(meta.get("total_pages") or meta.get("last_page") or page)
            if page >= total_pages or not rows:
                break
            page += 1
        return expenses

    async def get_expense(self, expense_id: str) -> Expense:
        response = await self._client.get(
            f"/api/v1/expenses/{expense_id}",
            params={"include": "vendor"},
        )
        self._raise_for_status(response)
        data = response.json().get("data") or response.json()
        return self._parse_expense(data)

    async def update_expense_custom_fields(
        self,
        expense_id: str,
        updates: dict[str, str],
    ) -> Expense:
        """Partial update of custom_value* fields."""
        response = await self._client.put(
            f"/api/v1/expenses/{expense_id}",
            json=updates,
        )
        self._raise_for_status(response)
        data = response.json().get("data") or response.json()
        return self._parse_expense(data)

    def _parse_expense(self, row: dict[str, Any]) -> Expense:
        vendor = row.get("vendor") or {}
        vendor_name = ""
        if isinstance(vendor, dict):
            vendor_name = str(vendor.get("name") or "")
        if not vendor_name:
            vendor_name = str(row.get("vendor_name") or "")

        amount_raw = row.get("amount") or 0
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            amount = 0.0

        return Expense(
            id=str(row.get("id") or ""),
            number=str(row.get("number") or ""),
            amount=amount,
            date=_parse_date(row.get("date")),
            vendor_name=vendor_name,
            public_notes=str(row.get("public_notes") or ""),
            private_notes=str(row.get("private_notes") or ""),
            custom_value1=str(row.get("custom_value1") or ""),
            custom_value2=str(row.get("custom_value2") or ""),
            custom_value3=str(row.get("custom_value3") or ""),
            custom_value4=str(row.get("custom_value4") or ""),
            updated_at=_parse_datetime(
                row.get("updated_at") or row.get("updated_at_unix")
            ),
            raw=row,
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        location = response.headers.get("Location", "")
        detail = response.text[:500]
        extra = f" Location={location}" if location else ""
        raise InvoiceNinjaError(
            f"Invoice Ninja HTTP {response.status_code}:{extra} {detail}".strip()
        )


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    try:
        only_date = date.fromisoformat(str(value)[:10])
        return datetime.combine(only_date, datetime.min.time())
    except ValueError:
        return None


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
