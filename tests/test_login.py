from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.settings import get_settings


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.main import create_app

    return TestClient(create_app())


def test_health_public(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_match_redirects_to_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/match", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_first_start_sets_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    page = client.get("/login")
    assert "Passwort festlegen" in page.text
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
    assert response.headers["location"] == "/match"

    client2 = _client(tmp_path, monkeypatch)
    page2 = client2.get("/login")
    assert "Anmelden" in page2.text
    bad = client2.post(
        "/login",
        data={"action": "login", "password": "nope"},
        follow_redirects=False,
    )
    assert bad.status_code == 400
    ok = client2.post(
        "/login",
        data={"action": "login", "password": "longenough"},
        follow_redirects=False,
    )
    assert ok.status_code == 303
    assert ok.headers["location"] == "/match"
