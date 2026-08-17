from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import HTTPException

from app.aliases import VendorAliasStore
from app.audit import AuditLog
from app.auth import AuthStore
from app.clients.invoiceninja import InvoiceNinjaClient
from app.clients.paperless import PaperlessClient
from app.settings import Settings, get_settings


def in_client(settings: Settings) -> InvoiceNinjaClient:
    return InvoiceNinjaClient(settings.invoice_ninja_url, settings.invoice_ninja_token)


def pl_client(settings: Settings) -> PaperlessClient:
    return PaperlessClient(settings.paperless_url, settings.paperless_token)


def alias_store(settings: Settings) -> VendorAliasStore:
    return VendorAliasStore(Path(settings.data_dir) / "vendor_aliases.json")


def audit_log(settings: Settings) -> AuditLog:
    return AuditLog(
        Path(settings.data_dir) / "audit.log",
        max_bytes=settings.audit_log_max_bytes,
    )


def auth_store(settings: Settings) -> AuthStore:
    return AuthStore(Path(settings.data_dir) / "auth.json")


def resolve_year(year: int | None) -> int:
    current = date.today().year
    if year is None:
        return current
    if year < 1990 or year > current + 1:
        raise HTTPException(status_code=400, detail="Invalid year")
    return year


def mapping_ready(settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    return (
        settings.mapping_complete
        and settings.in_configured
        and settings.pl_configured
    )
