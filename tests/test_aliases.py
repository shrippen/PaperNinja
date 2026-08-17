from __future__ import annotations

from pathlib import Path

from app.aliases import VendorAliasStore, normalize_name


def test_normalize_name() -> None:
    assert normalize_name("  DB AG ") == "db ag"
    assert normalize_name("Müller & Söhne GmbH") == "müller söhne gmbh"


def test_learn_and_equivalents(tmp_path: Path) -> None:
    path = tmp_path / "vendor_aliases.json"
    store = VendorAliasStore(path)
    assert store.learn("Deutsche Bahn", "DB AG")
    assert "db ag" in store.equivalents("Deutsche Bahn")
    assert "deutsche bahn" in store.equivalents("DB AG")

    store2 = VendorAliasStore(path)
    assert "db ag" in store2.equivalents("deutsche bahn")

    store2.learn("DB AG", "DB")
    equiv = store2.equivalents("DB")
    assert "db ag" in equiv
    assert "deutsche bahn" in equiv


def test_learn_ignores_identical(tmp_path: Path) -> None:
    store = VendorAliasStore(tmp_path / "a.json")
    assert store.learn("Same", "same") is False


def test_backfilled_flag(tmp_path: Path) -> None:
    path = tmp_path / "vendor_aliases.json"
    store = VendorAliasStore(path)
    assert store.backfilled is False
    store.learn("A GmbH", "A AG", persist=False)
    store.mark_backfilled()
    again = VendorAliasStore(path)
    assert again.backfilled is True
    assert "a ag" in again.equivalents("A GmbH")
