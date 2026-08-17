from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _ts() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class AuditLog:
    """Append-only text log with a hard size cap (trim oldest lines)."""

    def __init__(self, path: Path, max_bytes: int = 1_048_576) -> None:
        self.path = path
        self.max_bytes = max(16_384, max_bytes)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, action: str, **fields: object) -> None:
        parts = [f"{k}={_fmt(v)}" for k, v in fields.items() if v is not None and v != ""]
        line = f"{_ts()}\t{action}\t" + " ".join(parts) + "\n"
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        self._trim()

    def _trim(self) -> None:
        try:
            size = self.path.stat().st_size
        except OSError:
            return
        if size <= self.max_bytes:
            return
        keep = int(self.max_bytes * 0.6)
        data = self.path.read_bytes()
        cut = data[-keep:]
        newline = cut.find(b"\n")
        if newline != -1:
            cut = cut[newline + 1 :]
        self.path.write_bytes(cut)


def _fmt(value: object) -> str:
    text = str(value).replace("\t", " ").replace("\n", " ").strip()
    if " " in text:
        return f"\"{text}\""
    return text
