from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_HASHER = PasswordHasher()
MIN_PASSWORD_LENGTH = 8


class AuthError(Exception):
    pass


@dataclass(slots=True)
class AuthState:
    session_secret: str
    password_hash: str | None


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
        return AuthState(session_secret=str(secret), password_hash=hash_)

    def has_password(self) -> bool:
        return bool(self.state().password_hash)

    def session_secret(self) -> str:
        return self.state().session_secret

    def set_password(self, password: str) -> None:
        cleaned = password.strip()
        if len(cleaned) < MIN_PASSWORD_LENGTH:
            raise AuthError(
                f"Passwort muss mindestens {MIN_PASSWORD_LENGTH} Zeichen haben."
            )
        data = self._read()
        if not data.get("session_secret"):
            data["session_secret"] = secrets.token_hex(32)
        data["password_hash"] = _HASHER.hash(cleaned)
        self._write(data)

    def change_password(self, current: str, new: str) -> None:
        if not self.verify(current):
            raise AuthError("Aktuelles Passwort ist falsch.")
        self.set_password(new)

    def verify(self, password: str) -> bool:
        stored = self.state().password_hash
        if not stored:
            return False
        try:
            return _HASHER.verify(stored, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
