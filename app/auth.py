from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_HASHER = PasswordHasher()
MIN_PASSWORD_LENGTH = 8
_DUMMY_HASH: str | None = None


def _dummy_hash() -> str:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = _HASHER.hash("paperninja-dummy-not-a-password")
    return _DUMMY_HASH


class AuthError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class AuthState:
    session_secret: str
    password_hash: str | None
    auth_epoch: int


class AuthStore:
    """On-disk Argon2id hash + session signing secret. Not reversible encryption."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_secret()

    def _read(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AuthError(f"auth store unreadable: {exc}") from exc
        if not isinstance(raw, dict):
            raise AuthError("auth store is not an object")
        return raw

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _ensure_secret(self) -> None:
        data = self._read()
        if data.get("session_secret"):
            return
        data["session_secret"] = secrets.token_hex(32)
        if "password_hash" not in data:
            data["password_hash"] = None
        self._write(data)

    def state(self) -> AuthState:
        data = self._read()
        secret = data.get("session_secret")
        if not secret:
            self._ensure_secret()
            data = self._read()
            secret = data["session_secret"]
        hash_ = data.get("password_hash")
        if hash_ == "":
            hash_ = None
        try:
            epoch = int(data.get("auth_epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0
        return AuthState(
            session_secret=str(secret),
            password_hash=hash_,
            auth_epoch=epoch,
        )

    def auth_epoch(self) -> int:
        return self.state().auth_epoch

    def has_password(self) -> bool:
        return bool(self.state().password_hash)

    def session_valid(self, session: dict) -> bool:
        if not session.get("authenticated"):
            return False
        stored = self.auth_epoch()
        got = session.get("auth_epoch")
        if got is None:
            return stored == 0
        try:
            return int(got) == stored
        except (TypeError, ValueError):
            return False

    def _bump_epoch(self, data: dict) -> None:
        try:
            data["auth_epoch"] = int(data.get("auth_epoch") or 0) + 1
        except (TypeError, ValueError):
            data["auth_epoch"] = 1

    def session_secret(self) -> str:
        return self.state().session_secret

    def set_password(self, password: str) -> None:
        cleaned = password.strip()
        if len(cleaned) < MIN_PASSWORD_LENGTH:
            raise AuthError("password_too_short")
        data = self._read()
        if not data.get("session_secret"):
            data["session_secret"] = secrets.token_hex(32)
        data["password_hash"] = _HASHER.hash(cleaned)
        self._bump_epoch(data)
        self._write(data)

    def change_password(self, current: str, new: str) -> None:
        if not self.verify(current):
            raise AuthError("password_current")
        self.set_password(new)

    def verify(self, password: str) -> bool:
        stored = self.state().password_hash
        if not stored:
            try:
                _HASHER.verify(_dummy_hash(), password or "x")
            except (VerifyMismatchError, InvalidHashError):
                pass
            return False
        try:
            return _HASHER.verify(stored, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
