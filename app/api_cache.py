from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


class TtlCache:
    """In-process TTL cache. One process, no matching DB."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, object]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, key: str) -> object | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires <= time.monotonic():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: object, ttl_seconds: float) -> None:
        self._data[key] = (time.monotonic() + max(0.0, ttl_seconds), value)

    def invalidate(self, *prefixes: str) -> None:
        if not prefixes:
            self._data.clear()
            return
        for key in list(self._data):
            if any(key.startswith(prefix) for prefix in prefixes):
                self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()
        self._locks.clear()

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[T]],
        ttl_seconds: float,
    ) -> T:
        if ttl_seconds <= 0:
            return await factory()
        hit = self.get(key)
        if hit is not None:
            return hit  # type: ignore[return-value]
        async with self._lock_for(key):
            hit = self.get(key)
            if hit is not None:
                return hit  # type: ignore[return-value]
            value = await factory()
            self.set(key, value, ttl_seconds)
            return value


cache = TtlCache()


def invalidate_lists() -> None:
    cache.invalidate("in:", "pl:")
