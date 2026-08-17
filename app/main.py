from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.alias_backfill import backfill_vendor_aliases
from app.auth import AuthError, AuthStore, MIN_PASSWORD_LENGTH
from app.i18n import TRANSLATIONS, supported_lang
from app.deps import (
    alias_store,
    audit_log,
    auth_store,
    in_client,
    mapping_ready,
    pl_client,
    resolve_year,
)
from app.linking import link_expense_documents, unlink_expense_document
from app.linked_view import list_linked_pairs
from app.matching.scorer import (
    build_matches,
    build_reverse_matches,
    filter_expenses_by_year,
    filter_unlinked_documents,
    filter_unlinked_expenses,
    year_choices,
)
from app.search import PRESETS, search_documents_for_expense
from app.session_links import forget_link, remember_link
from app.settings import Settings, get_settings

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
PUBLIC_PATHS = frozenset({"/health", "/login", "/logout"})


def _combo_on(value: str | None) -> bool:
    return (value or "").lower() in {"1", "true", "on", "yes"}


def _parse_document_ids(document_id: int | None, document_ids: str) -> list[int]:
    ids: list[int] = []
    for part in (document_ids or "").replace(" ", ",").split(","):
        if part.strip().isdigit():
            ids.append(int(part.strip()))
    if document_id is not None:
        ids.append(document_id)
    return list(dict.fromkeys(ids))


def _safe_next(url: str | None) -> str:
    if url and url.startswith("/") and not url.startswith("//") and not url.startswith("/login"):
        return url
    return "/match"


def create_app() -> FastAPI:
    settings = get_settings()
    store = auth_store(settings)
    application = FastAPI(title="PaperNinja", version=__version__)
    application.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )

    @application.middleware("http")
    async def auth_gate(request: Request, call_next):
        request.state.auth_store = store
        lang = request.query_params.get("lang")
        if lang:
            request.session["lang"] = supported_lang(lang)
        path = request.url.path
        if path.startswith("/static") or path in PUBLIC_PATHS:
            return await call_next(request)
        if request.session.get("authenticated"):
            return await call_next(request)
        return RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=303)

    application.add_middleware(
        SessionMiddleware,
        secret_key=store.session_secret(),
        session_cookie="paperninja",
        same_site="lax",
        https_only=settings.session_https,
        max_age=14 * 24 * 3600,
    )
    # Expose translations to templates.
    templates.env.globals["I18N"] = TRANSLATIONS
    register_routes(application, store)
    return application


def register_routes(app: FastAPI, store: AuthStore) -> None:
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/login", response_class=HTMLResponse, response_model=None)
    async def login_page(request: Request, next: str = "", error: str = ""):
        if request.session.get("authenticated"):
            return RedirectResponse(_safe_next(next), status_code=302)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "version": __version__,
                "login_view": True,
                "setup": not store.has_password(),
                "next_url": _safe_next(next),
                "error": error,
                "message": "",
                "active": "",
                "min_password_length": MIN_PASSWORD_LENGTH,
            },
        )

    @app.post("/login", response_model=None)
    async def login_submit(
        request: Request,
        password: Annotated[str, Form()],
        action: Annotated[str, Form()] = "login",
        password_confirm: Annotated[str, Form()] = "",
        next: Annotated[str, Form()] = "",
    ):
        next_url = _safe_next(next)

        def render(error: str, setup: bool) -> HTMLResponse:
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "version": __version__,
                    "login_view": True,
                    "setup": setup,
                    "next_url": next_url,
                    "error": error,
                    "message": "",
                    "active": "",
                    "min_password_length": MIN_PASSWORD_LENGTH,
                },
                status_code=400,
            )

        if action == "setup":
            if store.has_password():
                return render("Passwort ist bereits gesetzt.", setup=False)
            if password != password_confirm:
                return render("Passwörter stimmen nicht überein.", setup=True)
            try:
                store.set_password(password)
            except AuthError as exc:
                return render(str(exc), setup=True)
            request.session["authenticated"] = True
            return RedirectResponse("/match", status_code=303)
        if not store.verify(password):
            return render("Passwort falsch.", setup=False)
        request.session["authenticated"] = True
        return RedirectResponse(next_url, status_code=303)

    @app.get("/logout")
    @app.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/password", response_class=HTMLResponse)
    async def password_page(request: Request, message: str = "", error: str = ""):
        return templates.TemplateResponse(
            request,
            "password.html",
            {"version": __version__, "active": "password", "message": message, "error": error},
        )

    @app.post("/password")
    async def password_change(
        request: Request,
        current: Annotated[str, Form()],
        password: Annotated[str, Form()],
        password_confirm: Annotated[str, Form()],
    ):
        if password != password_confirm:
            return RedirectResponse("/password?error=Passw%C3%B6rter+stimmen+nicht", status_code=303)
        try:
            store.change_password(current, password)
        except AuthError as exc:
            return RedirectResponse(f"/password?error={quote(str(exc))}", status_code=303)
        audit_log(get_settings()).write("password_change")
        return RedirectResponse("/password?message=Passwort+gespeichert", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def home() -> RedirectResponse:
        return RedirectResponse("/match", status_code=302)

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page(request: Request) -> HTMLResponse:
        settings = get_settings()
        in_fields: list[Any] = []
        pl_fields: list[Any] = []
        in_status = "not_configured"
        pl_status = "not_configured"
        in_error = ""
        pl_error = ""

        if settings.in_configured:
            client = in_client(settings)
            try:
                await client.ping()
                in_fields = await client.list_expense_custom_fields()
                in_status = "ok"
            except Exception as exc:
                in_status = "error"
                in_error = str(exc)
            finally:
                await client.aclose()

        if settings.pl_configured:
            client = pl_client(settings)
            try:
                await client.ping()
                pl_fields = await client.list_custom_fields()
                pl_status = "ok"
            except Exception as exc:
                pl_status = "error"
                pl_error = str(exc)
            finally:
                await client.aclose()

        env_snapshot = {
            "IN_EXPENSE_FIELD_INVOICE_NUMBER": settings.in_expense_field_invoice_number,
            "IN_EXPENSE_FIELD_PAPERLESS_URL": settings.in_expense_field_paperless_url,
            "PL_FIELD_INVOICE_NUMBER": settings.pl_field_invoice_number,
            "PL_FIELD_EXPENSE_NUMBER": settings.pl_field_expense_number,
            "PL_FIELD_INVOICE_NINJA_URL": settings.pl_field_invoice_ninja_url,
            "PL_FIELD_AMOUNT": settings.pl_field_amount,
            "PL_REVERSE_QUEUE_TAG": settings.pl_reverse_queue_tag,
        }
        return templates.TemplateResponse(
            request,
            "setup.html",
            {
                "version": __version__,
                "settings": settings,
                "in_fields": in_fields,
                "pl_fields": pl_fields,
                "in_status": in_status,
                "pl_status": pl_status,
                "in_error": in_error,
                "pl_error": pl_error,
                "env_snapshot": env_snapshot,
                "mapping_complete": settings.mapping_complete,
                "active": "setup",
            },
        )

    @app.get("/match", response_class=HTMLResponse)
    async def match_page(
        request: Request,
        year: int | None = None,
        combo: str = "",
        message: str = "",
        error: str = "",
    ) -> HTMLResponse:
        settings = get_settings()
        return templates.TemplateResponse(
            request,
            "match.html",
            {
                "version": __version__,
                "settings": settings,
                "blocked": not mapping_ready(settings),
                "message": message,
                "error": error,
                "active": "match",
                "year": resolve_year(year),
                "years": year_choices(),
                "combo": _combo_on(combo),
            },
        )

    @app.get("/match/results", response_class=HTMLResponse)
    async def match_results(
        request: Request,
        year: int | None = None,
        combo: str = "",
    ) -> HTMLResponse:
        settings = get_settings()
        selected_year = resolve_year(year)
        include_combos = _combo_on(combo)
        if not mapping_ready(settings):
            return templates.TemplateResponse(
                request,
                "match_results.html",
                {
                    "settings": settings,
                    "matches": [],
                    "blocked": True,
                    "year": selected_year,
                    "combo": include_combos,
                    "expense_count": 0,
                    "document_count": 0,
                    "error": "",
                },
            )
        aliases = alias_store(settings)
        ic = in_client(settings)
        pc = pl_client(settings)
        try:
            all_expenses, documents = await asyncio.gather(
                ic.list_expenses(),
                pc.list_documents(
                    created_gte=date(selected_year, 1, 1),
                    created_lte=date(selected_year, 12, 31),
                ),
            )
            await backfill_vendor_aliases(
                settings=settings,
                in_client=ic,
                pl_client=pc,
                aliases=aliases,
                expenses=all_expenses,
            )
            expenses = filter_expenses_by_year(
                filter_unlinked_expenses(all_expenses, settings),
                selected_year,
            )
            documents = filter_unlinked_documents(documents, settings)
            matches = build_matches(
                expenses,
                documents,
                settings,
                aliases=aliases,
                include_combos=include_combos,
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "match_results.html",
                {
                    "settings": settings,
                    "matches": [],
                    "blocked": False,
                    "year": selected_year,
                    "combo": include_combos,
                    "expense_count": 0,
                    "document_count": 0,
                    "error": str(exc),
                },
                status_code=500,
            )
        finally:
            await ic.aclose()
            await pc.aclose()
        return templates.TemplateResponse(
            request,
            "match_results.html",
            {
                "settings": settings,
                "matches": matches,
                "blocked": False,
                "year": selected_year,
                "combo": include_combos,
                "expense_count": len(expenses),
                "document_count": len(documents),
                "error": "",
            },
        )

    @app.get("/search", response_class=HTMLResponse)
    async def search_documents(
        request: Request,
        expense_id: str,
        year: int | None = None,
        q: str = "",
        preset: str = "",
        unlinked_only: str = "",
        date_from: str = "",
        date_to: str = "",
        correspondent: str = "",
    ) -> HTMLResponse:
        settings = get_settings()
        selected_year = resolve_year(year)
        ic = in_client(settings)
        pc = pl_client(settings)
        error = ""
        hits = []
        expense = None
        try:
            expense = await ic.get_expense(expense_id)
            hits = await search_documents_for_expense(
                pl_client=pc,
                settings=settings,
                expense=expense,
                year=selected_year,
                q=q,
                preset=preset,
                unlinked_only=bool(unlinked_only),
                date_from=date_from,
                date_to=date_to,
                correspondent=correspondent,
            )
        except Exception as exc:
            error = str(exc)
        finally:
            await ic.aclose()
            await pc.aclose()
        if expense is None:
            raise HTTPException(status_code=404, detail="Expense not found")
        return templates.TemplateResponse(
            request,
            "search_panel.html",
            {
                "settings": settings,
                "expense": expense,
                "year": selected_year,
                "q": q,
                "preset": preset,
                "presets": PRESETS,
                "unlinked_only": bool(unlinked_only),
                "date_from": date_from,
                "date_to": date_to,
                "correspondent": correspondent,
                "hits": hits,
                "searched": True,
                "error": error,
            },
        )

    @app.get("/linked", response_class=HTMLResponse)
    async def linked_page(request: Request, message: str = "", error: str = "") -> HTMLResponse:
        settings = get_settings()
        ic = in_client(settings)
        pc = pl_client(settings)
        pairs, source = [], "session"
        try:
            pairs, source = await list_linked_pairs(
                session=request.session,
                settings=settings,
                in_client=ic,
                pl_client=pc,
            )
        except Exception as exc:
            error = error or str(exc)
        finally:
            await ic.aclose()
            await pc.aclose()
        return templates.TemplateResponse(
            request,
            "linked.html",
            {
                "version": __version__,
                "settings": settings,
                "pairs": pairs,
                "source": source,
                "message": message,
                "error": error,
                "active": "linked",
            },
        )

    @app.get("/queue", response_class=HTMLResponse)
    async def queue_page(
        request: Request,
        year: int | None = None,
        message: str = "",
        error: str = "",
    ) -> HTMLResponse:
        settings = get_settings()
        return templates.TemplateResponse(
            request,
            "queue.html",
            {
                "version": __version__,
                "settings": settings,
                "active": "queue",
                "year": resolve_year(year),
                "years": year_choices(),
                "message": message,
                "error": error,
            },
        )

    @app.get("/queue/results", response_class=HTMLResponse)
    async def queue_results(request: Request, year: int | None = None) -> HTMLResponse:
        settings = get_settings()
        selected_year = resolve_year(year)
        if not mapping_ready(settings) or not settings.pl_reverse_queue_tag:
            return templates.TemplateResponse(
                request,
                "queue_results.html",
                {
                    "settings": settings,
                    "matches": [],
                    "year": selected_year,
                    "document_count": 0,
                    "expense_count": 0,
                    "tag_ok": False,
                    "error": "",
                },
            )
        aliases = alias_store(settings)
        ic = in_client(settings)
        pc = pl_client(settings)
        try:
            tag_id = await pc.resolve_tag_id(settings.pl_reverse_queue_tag)
            if tag_id is None:
                return templates.TemplateResponse(
                    request,
                    "queue_results.html",
                    {
                        "settings": settings,
                        "matches": [],
                        "year": selected_year,
                        "document_count": 0,
                        "expense_count": 0,
                        "tag_ok": False,
                        "error": "",
                    },
                )
            all_expenses, tagged = await asyncio.gather(
                ic.list_expenses(),
                pc.search_documents(
                    tags_id_in=[tag_id],
                    created_gte=date(selected_year, 1, 1),
                    created_lte=date(selected_year, 12, 31),
                    page_size=50,
                    max_pages=2,
                ),
            )
            await backfill_vendor_aliases(
                settings=settings,
                in_client=ic,
                pl_client=pc,
                aliases=aliases,
                expenses=all_expenses,
            )
            documents = filter_unlinked_documents(tagged, settings)
            expenses = filter_expenses_by_year(
                filter_unlinked_expenses(all_expenses, settings),
                selected_year,
            )
            matches = build_reverse_matches(documents, expenses, settings, aliases=aliases)
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "queue_results.html",
                {
                    "settings": settings,
                    "matches": [],
                    "year": selected_year,
                    "document_count": 0,
                    "expense_count": 0,
                    "tag_ok": True,
                    "error": str(exc),
                },
                status_code=500,
            )
        finally:
            await ic.aclose()
            await pc.aclose()
        return templates.TemplateResponse(
            request,
            "queue_results.html",
            {
                "settings": settings,
                "matches": matches,
                "year": selected_year,
                "document_count": len(documents),
                "expense_count": len(expenses),
                "tag_ok": True,
                "error": "",
            },
        )

    @app.get("/preview/{document_id}/thumb")
    async def preview_thumb(document_id: int) -> Response:
        settings = get_settings()
        client = pl_client(settings)
        try:
            data, content_type = await client.fetch_document_bytes(document_id, "thumb")
        finally:
            await client.aclose()
        return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, max-age=300"})

    @app.get("/preview/{document_id}/preview")
    async def preview_document(document_id: int) -> Response:
        settings = get_settings()
        client = pl_client(settings)
        try:
            data, content_type = await client.fetch_document_bytes(document_id, "preview")
        finally:
            await client.aclose()
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "private, max-age=300",
                "Content-Disposition": f'inline; filename="document-{document_id}.pdf"',
            },
        )

    @app.post("/link")
    async def link_action(
        request: Request,
        expense_id: Annotated[str, Form()],
        document_id: Annotated[int | None, Form()] = None,
        document_ids: Annotated[str, Form()] = "",
        year: Annotated[int | None, Form()] = None,
        return_to: Annotated[str, Form()] = "match",
        combo: Annotated[str, Form()] = "",
    ) -> RedirectResponse:
        settings = get_settings()
        selected_year = resolve_year(year)
        combo_q = "&combo=1" if _combo_on(combo) else ""

        def fail(message: str) -> RedirectResponse:
            if return_to == "queue":
                url = f"/queue?year={selected_year}&error={quote(message)}"
            else:
                url = f"/match?year={selected_year}{combo_q}&error={quote(message)}"
            return RedirectResponse(url, status_code=303)

        if not settings.mapping_complete:
            return fail("Mapping unvollständig")
        ids = _parse_document_ids(document_id, document_ids)
        if not ids:
            return fail("Kein Dokument")
        ic = in_client(settings)
        pc = pl_client(settings)
        aliases = alias_store(settings)
        audit = audit_log(settings)
        try:
            expense = await ic.get_expense(expense_id)
            documents = await pc.get_documents_by_ids(ids)
            by_id = {doc.id: doc for doc in documents}
            ordered = [by_id[i] for i in ids if i in by_id]
            if len(ordered) != len(ids):
                raise ValueError("Document not found")
            results = await link_expense_documents(
                settings=settings,
                in_client=ic,
                pl_client=pc,
                expense=expense,
                documents=ordered,
            )
            now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            for result in results:
                aliases.learn(result.vendor_name, result.correspondent_name)
                remember_link(
                    request.session,
                    {
                        "expense_id": result.expense_id,
                        "document_id": result.document_id,
                        "expense_number": result.expense_number,
                        "document_title": result.document_title,
                        "linked_at": now,
                    },
                )
                audit.write(
                    "link",
                    expense_id=result.expense_id,
                    document_id=result.document_id,
                    expense_number=result.expense_number,
                )
            doc_label = ", ".join(str(r.document_id) for r in results)
            msg = f"Verknüpft: {results[0].expense_number} ↔ Doc {doc_label}"
            if return_to == "queue":
                url = f"/queue?year={selected_year}&message={quote(msg)}"
            else:
                url = f"/match?year={selected_year}{combo_q}&message={quote(msg)}"
            return RedirectResponse(url, status_code=303)
        except Exception as exc:
            return fail(str(exc))
        finally:
            await ic.aclose()
            await pc.aclose()

    @app.post("/unlink")
    async def unlink_action(
        request: Request,
        expense_id: Annotated[str, Form()],
        document_id: Annotated[int, Form()],
    ) -> RedirectResponse:
        settings = get_settings()
        ic = in_client(settings)
        pc = pl_client(settings)
        audit = audit_log(settings)
        try:
            expense = await ic.get_expense(expense_id)
            document = await pc.get_document(document_id)
            await unlink_expense_document(
                settings=settings,
                in_client=ic,
                pl_client=pc,
                expense=expense,
                document=document,
            )
            forget_link(request.session, expense_id, document_id)
            audit.write("unlink", expense_id=expense_id, document_id=document_id)
            return RedirectResponse(
                f"/linked?message={quote('Entkoppelt')}",
                status_code=303,
            )
        except Exception as exc:
            return RedirectResponse(
                f"/linked?error={quote(str(exc))}",
                status_code=303,
            )
        finally:
            await ic.aclose()
            await pc.aclose()


app = create_app()
