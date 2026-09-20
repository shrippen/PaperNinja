from __future__ import annotations

import asyncio
import time

import pytest

from app.api_cache import TtlCache, cache, invalidate_lists
from app.lists import cached_documents, cached_expenses, cached_tagged_documents
from app.settings import Settings


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    cache.clear()
    yield
    cache.clear()


def test_ttl_expires() -> None:
    store = TtlCache()
    store.set("k", "v", 0.05)
    assert store.get("k") == "v"
    time.sleep(0.06)
    assert store.get("k") is None


def test_invalidate_by_prefix() -> None:
    cache.set("in:expenses:all", [1], 60)
    cache.set("in:expenses:2026", [2], 60)
    cache.set("pl:documents:2026", [3], 60)
    cache.invalidate("in:")
    assert cache.get("in:expenses:all") is None
    assert cache.get("pl:documents:2026") == [3]
    invalidate_lists()
    assert cache.get("pl:documents:2026") is None


async def test_get_or_set_single_flight() -> None:
    store = TtlCache()
    n = 0

    async def factory() -> str:
        nonlocal n
        n += 1
        await asyncio.sleep(0.05)
        return "ok"

    results = await asyncio.gather(
        *(store.get_or_set("k", factory, 30) for _ in range(8))
    )
    assert n == 1
    assert results == ["ok"] * 8


async def test_ttl_zero_skips_cache() -> None:
    store = TtlCache()
    n = 0

    async def factory() -> int:
        nonlocal n
        n += 1
        return n

    assert await store.get_or_set("k", factory, 0) == 1
    assert await store.get_or_set("k", factory, 0) == 2


class _FakeIN:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_expenses(self, **kwargs) -> list[str]:
        self.calls.append(kwargs)
        return ["exp"]


class _FakePL:
    def __init__(self) -> None:
        self.list_calls = 0
        self.search_calls = 0

    async def list_documents(self, **kwargs) -> list[str]:
        self.list_calls += 1
        return ["doc"]

    async def search_documents(self, **kwargs) -> list[str]:
        self.search_calls += 1
        return ["tagged"]


async def test_cached_expenses_reuses_ttl() -> None:
    settings = Settings(api_cache_ttl_seconds=180)
    client = _FakeIN()
    first = await cached_expenses(client, settings, year=2026)  # type: ignore[arg-type]
    second = await cached_expenses(client, settings, year=2026)  # type: ignore[arg-type]
    assert first == second == ["exp"]
    assert len(client.calls) == 1
    assert client.calls[0]["date_from"].year == 2026
    assert client.calls[0]["date_to"].month == 12


async def test_cached_documents_and_tags() -> None:
    settings = Settings(api_cache_ttl_seconds=180)
    client = _FakePL()
    await cached_documents(client, settings, 2026)  # type: ignore[arg-type]
    await cached_documents(client, settings, 2026)  # type: ignore[arg-type]
    await cached_tagged_documents(client, settings, 9, 2026)  # type: ignore[arg-type]
    await cached_tagged_documents(client, settings, 9, 2026)  # type: ignore[arg-type]
    assert client.list_calls == 1
    assert client.search_calls == 1
