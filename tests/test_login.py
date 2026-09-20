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


def test_setup_password_mismatch_english(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.get("/login?lang=en")
    response = client.post(
        "/login",
        data={
            "action": "setup",
            "password": "longenough",
            "password_confirm": "otherpass",
        },
    )
    assert response.status_code == 400
    assert "Passwords do not match" in response.text


def test_login_lockout_after_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.login_limit import LoginLimiter
    from app.main import create_app
    import app.main as mainmod

    tight = LoginLimiter(max_failures=3, lock_seconds=45, fail_delay_seconds=0)
    monkeypatch.setattr(mainmod, "login_limiter", tight)
    client = TestClient(create_app())
    setup = client.post(
        "/login",
        data={
            "action": "setup",
            "password": "longenough",
            "password_confirm": "longenough",
        },
        follow_redirects=False,
    )
    assert setup.status_code == 303
    client.post("/logout")

    for _ in range(2):
        bad = client.post(
            "/login",
            data={"action": "login", "password": "wrong-password"},
            follow_redirects=False,
        )
        assert bad.status_code == 400
        assert "Passwort falsch" in bad.text

    locked = client.post(
        "/login",
        data={"action": "login", "password": "wrong-password"},
        follow_redirects=False,
    )
    assert locked.status_code == 429
    assert "45" in locked.text

    still = client.post(
        "/login",
        data={"action": "login", "password": "longenough"},
        follow_redirects=False,
    )
    assert still.status_code == 429


def test_password_change_mismatch_uses_i18n(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.post(
        "/login",
        data={
            "action": "setup",
            "password": "longenough",
            "password_confirm": "longenough",
        },
    )
    client.get("/password?lang=en")
    response = client.post(
        "/password",
        data={
            "current": "longenough",
            "password": "newpassword",
            "password_confirm": "mismatchx",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "password_mismatch" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "Passwords do not match" in page.text


def test_password_change_invalidates_other_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.main import create_app

    first = TestClient(create_app())
    assert (
        first.post(
            "/login",
            data={
                "action": "setup",
                "password": "longenough",
                "password_confirm": "longenough",
            },
            follow_redirects=False,
        ).status_code
        == 303
    )
    get_settings.cache_clear()
    second = TestClient(create_app())
    assert (
        second.post(
            "/login",
            data={"action": "login", "password": "longenough"},
            follow_redirects=False,
        ).status_code
        == 303
    )
    changed = first.post(
        "/password",
        data={
            "current": "longenough",
            "password": "newpassword",
            "password_confirm": "newpassword",
        },
        follow_redirects=False,
    )
    assert changed.status_code == 303
    dropped = second.get("/match", follow_redirects=False)
    assert dropped.status_code == 303
    assert dropped.headers["location"].startswith("/login")
    still = first.get("/match", follow_redirects=False)
    assert still.status_code == 200
