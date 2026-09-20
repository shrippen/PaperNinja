from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.auth import AuthError, AuthStore, MIN_PASSWORD_LENGTH


def test_set_and_verify_password(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    assert store.has_password() is False
    store.set_password("correct-horse")
    assert store.has_password() is True
    assert store.verify("correct-horse") is True
    assert store.verify("wrong") is False
    raw = json.loads((tmp_path / "auth.json").read_text())
    assert raw["password_hash"].startswith("$argon2")
    assert "correct-horse" not in (tmp_path / "auth.json").read_text()


def test_password_too_short(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    with pytest.raises(AuthError) as exc:
        store.set_password("x" * (MIN_PASSWORD_LENGTH - 1))
    assert exc.value.code == "password_too_short"


def test_verify_without_password_is_false(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    assert store.verify("anything-long") is False
    assert store.has_password() is False
    assert store.auth_epoch() == 0


def test_password_change_bumps_epoch(tmp_path: Path) -> None:
    store = AuthStore(tmp_path / "auth.json")
    store.set_password("correct-horse")
    first = store.auth_epoch()
    store.change_password("correct-horse", "other-horse")
    assert store.auth_epoch() == first + 1
    assert store.session_valid({"authenticated": True, "auth_epoch": first}) is False
    assert store.session_valid({"authenticated": True, "auth_epoch": store.auth_epoch()})


def test_session_secret_stable(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    first = AuthStore(path).session_secret()
    second = AuthStore(path).session_secret()
    assert first == second
    assert len(first) >= 32
