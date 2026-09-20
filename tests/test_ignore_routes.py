from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.settings import get_settings


def _authed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.main import create_app

    client = TestClient(create_app())
    response = client.post(
        "/login",
        data={
            "action": "setup",
            "password": "longenough",
            "password_confirm": "longenough",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def test_ignored_requires_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.main import create_app

    client = TestClient(create_app())
    response = client.get("/ignored", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_ignore_expense_and_unignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(tmp_path, monkeypatch)
    response = client.post(
        "/ignore",
        data={
            "kind": "expense",
            "expense_id": "exp-99",
            "year": "2026",
            "return_to": "match",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Ignoriert" in response.headers["location"]
    payload = json.loads((tmp_path / "ignore.json").read_text(encoding="utf-8"))
    assert payload["expenses"] == ["exp-99"]

    page = client.get("/ignored")
    assert page.status_code == 200
    assert "exp-99" in page.text
    assert "Ignoriert" in page.text

    undone = client.post(
        "/unignore",
        data={"kind": "expense", "expense_id": "exp-99"},
        follow_redirects=False,
    )
    assert undone.status_code == 303
    payload = json.loads((tmp_path / "ignore.json").read_text(encoding="utf-8"))
    assert payload["expenses"] == []
    empty = client.get("/ignored")
    assert "Nichts ignoriert" in empty.text


def test_ignore_document_english(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(tmp_path, monkeypatch)
    client.get("/ignored?lang=en")
    response = client.post(
        "/ignore",
        data={
            "kind": "document",
            "document_id": "42",
            "year": "2026",
            "return_to": "queue",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Ignored" in response.headers["location"]
    page = client.get("/ignored")
    assert "42" in page.text
    assert "Receipts" in page.text or "Show again" in page.text


def test_ignore_invalid_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(tmp_path, monkeypatch)
    response = client.post(
        "/ignore",
        data={"kind": "vendor", "year": "2026", "return_to": "match"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "Ungültiger" in unquote(response.headers["location"])


def test_ignore_writes_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(tmp_path, monkeypatch)
    client.post(
        "/ignore",
        data={
            "kind": "expense",
            "expense_id": "e1",
            "year": "2026",
            "return_to": "match",
        },
    )
    client.post("/unignore", data={"kind": "expense", "expense_id": "e1"})
    text = (tmp_path / "audit.log").read_text(encoding="utf-8")
    assert "\tignore\t" in text
    assert "\tunignore\t" in text
    assert "expense_id=e1" in text


def test_nav_includes_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _authed(tmp_path, monkeypatch)
    page = client.get("/setup")
    assert 'href="/ignored"' in page.text
