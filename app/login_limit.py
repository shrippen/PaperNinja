from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class _Attempt:
    failures: int = 0
    locked_until: float = 0.0


class LoginLimiter:
    """In-process lockout after repeated failed logins (single operator)."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        lock_seconds: float = 60.0,
        fail_delay_seconds: float = 0.4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max(1, max_failures)
        self.lock_seconds = max(0.0, lock_seconds)
        self.fail_delay_seconds = max(0.0, fail_delay_seconds)
        self._clock = clock
        self._attempts: dict[str, _Attempt] = {}

    def reset(self) -> None:
        self._attempts.clear()

    def locked_seconds(self, key: str) -> float:
        attempt = self._attempts.get(key)
        if attempt is None:
            return 0.0
        remaining = attempt.locked_until - self._clock()
        if remaining <= 0:
            if attempt.failures == 0:
                self._attempts.pop(key, None)
            else:
                attempt.locked_until = 0.0
            return 0.0
        return remaining

    def fail(self, key: str) -> float:
        remaining = self.locked_seconds(key)
        if remaining > 0:
            return remaining
        attempt = self._attempts.setdefault(key, _Attempt())
        attempt.failures += 1
        if attempt.failures >= self.max_failures:
            attempt.failures = 0
            attempt.locked_until = self._clock() + self.lock_seconds
            return self.lock_seconds
        return 0.0

    def success(self, key: str) -> None:
        self._attempts.pop(key, None)

    async def wait_fail(self) -> None:
        if self.fail_delay_seconds > 0:
            await asyncio.sleep(self.fail_delay_seconds)


limiter = LoginLimiter()
