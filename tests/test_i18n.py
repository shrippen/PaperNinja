from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.i18n import TRANSLATIONS, SUPPORTED_LANGS, supported_lang, t
from app.settings import get_settings


def test_supported_langs_are_de_en() -> None:
    assert set(SUPPORTED_LANGS) == {"de", "en"}
    assert set(TRANSLATIONS) == {"de", "en"}


def test_de_and_en_keys_match() -> None:
    assert set(TRANSLATIONS["de"]) == set(TRANSLATIONS["en"])
    assert TRANSLATIONS["de"].keys()
    empty = [key for key, value in TRANSLATIONS["en"].items() if not value.strip()]
    assert empty == []


def test_unknown_lang_falls_back_to_de() -> None:
    assert supported_lang("fr") == "de"
    assert supported_lang(None) == "de"
    assert supported_lang("EN") == "en"


def test_t_replaces_placeholders() -> None:
    assert "42" in t("de", "err_login_locked", seconds=42)
    assert "42" in t("en", "err_login_locked", seconds=42)
    assert t("en", "does_not_exist") == "does_not_exist"


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    from app.main import create_app

    return TestClient(create_app())


def test_login_english(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    page = client.get("/login?lang=en")
    assert page.status_code == 200
    assert 'lang="en"' in page.text
    assert "Set password" in page.text
    assert "Passwort festlegen" not in page.text
    assert "/static/htmx.min.js" in page.text
    assert "unpkg.com" not in page.text
    assert "cdn." not in page.text.lower()


def test_login_default_german(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    page = client.get("/login")
    assert 'lang="de"' in page.text
    assert "Passwort festlegen" in page.text


def test_htmx_is_local(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/static/htmx.min.js")
    assert response.status_code == 200
    assert 'version:"2.0.4"' in response.text
    page = client.get("/login")
    assert page.headers.get("content-security-policy", "").find("script-src 'self'") >= 0
    assert page.headers.get("x-frame-options") == "DENY"
    assert page.headers.get("x-content-type-options") == "nosniff"
