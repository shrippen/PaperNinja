from __future__ import annotations

import json
from pathlib import Path

from app.clients.invoiceninja import Expense
from app.clients.paperless import Document


class IgnoreStore:
    """Operator hide-list for expenses and documents. Not matching state."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._expenses, self._documents = self._load()

    def _load(self) -> tuple[set[str], set[int]]:
        if not self.path.exists():
            return set(), set()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set(), set()
        if not isinstance(raw, dict):
            return set(), set()
        expenses: set[str] = set()
        for item in raw.get("expenses") or []:
            text = str(item).strip()
            if text:
                expenses.add(text)
        documents: set[int] = set()
        for item in raw.get("documents") or []:
            try:
                documents.add(int(item))
            except (TypeError, ValueError):
                continue
        return expenses, documents

    def _save(self) -> None:
        payload = {
            "expenses": sorted(self._expenses),
            "documents": sorted(self._documents),
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def expense_ids(self) -> list[str]:
        return sorted(self._expenses)

    def document_ids(self) -> list[int]:
        return sorted(self._documents)

    def is_expense_ignored(self, expense_id: str) -> bool:
        return str(expense_id).strip() in self._expenses

    def is_document_ignored(self, document_id: int) -> bool:
        return int(document_id) in self._documents

    def ignore_expense(self, expense_id: str) -> bool:
        key = str(expense_id).strip()
        if not key or key in self._expenses:
            return False
        self._expenses.add(key)
        self._save()
        return True

    def ignore_document(self, document_id: int) -> bool:
        key = int(document_id)
        if key in self._documents:
            return False
        self._documents.add(key)
        self._save()
        return True

    def unignore_expense(self, expense_id: str) -> bool:
        key = str(expense_id).strip()
        if key not in self._expenses:
            return False
        self._expenses.discard(key)
        self._save()
        return True

    def unignore_document(self, document_id: int) -> bool:
        key = int(document_id)
        if key not in self._documents:
            return False
        self._documents.discard(key)
        self._save()
        return True

    def drop_expenses(self, expenses: list[Expense]) -> list[Expense]:
        if not self._expenses:
            return expenses
        return [item for item in expenses if item.id not in self._expenses]

    def drop_documents(self, documents: list[Document]) -> list[Document]:
        if not self._documents:
            return documents
        return [item for item in documents if item.id not in self._documents]
