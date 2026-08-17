from __future__ import annotations

from pathlib import Path

from app.audit import AuditLog


def test_audit_writes_and_trims(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    log = AuditLog(path, max_bytes=20_480)
    for i in range(200):
        log.write("link", expense_id=f"e{i}", document_id=i)
    assert path.exists()
    assert path.stat().st_size <= 20_480
    text = path.read_text(encoding="utf-8")
    assert "link" in text
    assert "expense_id=" in text


def test_audit_skips_empty_fields(tmp_path: Path) -> None:
    path = tmp_path / "audit.log"
    log = AuditLog(path)
    log.write("unlink", expense_id="abc", document_id=1, note="")
    line = path.read_text(encoding="utf-8").strip()
    assert "note=" not in line
    assert "expense_id=abc" in line
