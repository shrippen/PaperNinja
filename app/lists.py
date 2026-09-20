from __future__ import annotations

from datetime import date

from app.api_cache import cache
from app.clients.invoiceninja import Expense, InvoiceNinjaClient
from app.clients.paperless import Document, PaperlessClient
from app.settings import Settings


async def cached_expenses(
    client: InvoiceNinjaClient,
    settings: Settings,
    year: int | None = None,
) -> list[Expense]:
    ttl = settings.api_cache_ttl_seconds
    if year is None:
        return await cache.get_or_set("in:expenses:all", client.list_expenses, ttl)

    async def load_year() -> list[Expense]:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        result = await client.list_expenses(date_from=start, date_to=end)
        if InvoiceNinjaClient._date_filter_ok is False and ttl > 0:
            cache.set("in:expenses:all", result, ttl)
        return result

    return await cache.get_or_set(f"in:expenses:{year}", load_year, ttl)


async def cached_documents(
    client: PaperlessClient,
    settings: Settings,
    year: int,
) -> list[Document]:
    ttl = settings.api_cache_ttl_seconds
    start = date(year, 1, 1)
    end = date(year, 12, 31)

    async def load() -> list[Document]:
        return await client.list_documents(created_gte=start, created_lte=end)

    return await cache.get_or_set(f"pl:documents:{year}", load, ttl)


async def cached_tagged_documents(
    client: PaperlessClient,
    settings: Settings,
    tag_id: int,
    year: int,
) -> list[Document]:
    ttl = settings.api_cache_ttl_seconds
    start = date(year, 1, 1)
    end = date(year, 12, 31)

    async def load() -> list[Document]:
        return await client.search_documents(
            tags_id_in=[tag_id],
            created_gte=start,
            created_lte=end,
            page_size=50,
            max_pages=2,
        )

    return await cache.get_or_set(f"pl:documents:tag:{tag_id}:{year}", load, ttl)
