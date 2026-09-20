from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from math import ceil
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
from app.api_cache import invalidate_lists
from app.auth import AuthError, AuthStore, MIN_PASSWORD_LENGTH
from app.i18n import TRANSLATIONS, supported_lang, t
from app.lists import cached_documents, cached_expenses, cached_tagged_documents
from app.deps import (
    alias_store,
    audit_log,
    auth_store,
    ignore_store,
    in_client,
    mapping_ready,
    pl_client,
    resolve_year,
)
from app.linking import link_expense_documents, unlink_expense_document
from app.linked_view import list_linked_pairs
from app.login_limit import limiter as login_limiter
from app.matching.scorer import (
    build_matches,
    build_reverse_matches,
    filter_expenses_by_year,
    filter_unlinked_documents,
    filter_unlinked_expenses,
    year_choices,
)
from app.search import PRESET_KEYS, search_documents_for_expense
from app.session_links import forget_link, remember_link
from app.settings import Settings, get_settings

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
PUBLIC_PATHS = frozenset({"/health", "/login", "/logout"})
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


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


def _lang(request: Request) -> str:
    return supported_lang(request.session.get("lang"))


def _tr(request: Request, key: str, **kwargs: Any) -> str:
    return t(_lang(request), key, **kwargs)


def _page(request: Request, **extra: Any) -> dict[str, Any]:
    lang = _lang(request)
    ctx: dict[str, Any] = {
        "version": __version__,
        "lang": lang,
        "T": TRANSLATIONS[lang],
        "message": "",
        "error": "",
        "active": "",
        "login_view": False,
        "min_password_length": MIN_PASSWORD_LENGTH,
    }
    ctx.update(extra)
    return ctx


def _apply_security_headers(response: Response) -> Response:
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    return response


def _begin_session(request: Request, store: AuthStore) -> None:
    lang = request.session.get("lang")
    request.session.clear()
    if lang:
        request.session["lang"] = lang
    request.session["authenticated"] = True
    request.session["auth_epoch"] = store.auth_epoch()


def _client_key(request: Request) -> str:
    settings = get_settings()
    if settings.session_https:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


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
            response = await call_next(request)
            return _apply_security_headers(response)
        if request.session.get("authenticated"):
            if not store.session_valid(request.session):
                request.session.clear()
                return _apply_security_headers(
                    RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=303)
                )
            response = await call_next(request)
            return _apply_security_headers(response)
        return _apply_security_headers(
            RedirectResponse(f"/login?next={quote(path, safe='/')}", status_code=303)
        )

    application.add_middleware(
        SessionMiddleware,
        secret_key=store.session_secret(),
        session_cookie="paperninja",
        same_site="lax",
        https_only=settings.session_https,
        max_age=14 * 24 * 3600,
    )
    templates.env.globals["I18N"] = TRANSLATIONS
    templates.env.globals["SUPPORTED_LANGS"] = TRANSLATIONS.keys()
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
            _page(
                request,
                login_view=True,
                setup=not store.has_password(),
                next_url=_safe_next(next),
                error=error,
            ),
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

        def render(error: str, setup: bool, status_code: int = 400) -> HTMLResponse:
            return templates.TemplateResponse(
                request,
                "login.html",
                _page(
                    request,
                    login_view=True,
                    setup=setup,
                    next_url=next_url,
                    error=error,
                ),
                status_code=status_code,
            )

        key = _client_key(request)
        if action != "setup":
            locked = login_limiter.locked_seconds(key)
            if locked > 0:
                return render(
                    _tr(request, "err_login_locked", seconds=max(1, int(ceil(locked)))),
                    setup=False,
                    status_code=429,
                )

        if action == "setup":
            if store.has_password():
                return render(_tr(request, "err_password_already_set"), setup=False)
            if password != password_confirm:
                return render(_tr(request, "err_password_mismatch"), setup=True)
            try:
                store.set_password(password)
            except AuthError as exc:
                return render(_tr(request, f"err_{exc.code}", n=MIN_PASSWORD_LENGTH), setup=True)
            login_limiter.success(key)
            _begin_session(request, store)
            return RedirectResponse("/match", status_code=303)
        if not store.verify(password):
            await login_limiter.wait_fail()
            remaining = login_limiter.fail(key)
            if remaining > 0:
                return render(
                    _tr(request, "err_login_locked", seconds=max(1, int(ceil(remaining)))),
                    setup=False,
                    status_code=429,
                )
            return render(_tr(request, "err_password_wrong"), setup=False)
        login_limiter.success(key)
        _begin_session(request, store)
        return RedirectResponse(next_url, status_code=303)

    @app.get("/logout")
    @app.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/password", response_class=HTMLResponse)
    async def password_page(request: Request, message: str = "", error: str = ""):
        error_text = ""
        if error:
            error_text = _tr(request, f"err_{error}", n=MIN_PASSWORD_LENGTH)
            if error_text == f"err_{error}":
                error_text = error
        message_text = _tr(request, "msg_password_saved") if message == "saved" else message
        return templates.TemplateResponse(
            request,
            "password.html",
            _page(request, active="password", message=message_text, error=error_text),
        )

    @app.post("/password")
    async def password_change(
        request: Request,
        current: Annotated[str, Form()],
        password: Annotated[str, Form()],
        password_confirm: Annotated[str, Form()],
    ):
        if password != password_confirm:
            return RedirectResponse("/password?error=password_mismatch", status_code=303)
        try:
            store.change_password(current, password)
        except AuthError as exc:
            return RedirectResponse(f"/password?error={quote(exc.code)}", status_code=303)
        audit_log(get_settings()).write("password_change")
        _begin_session(request, store)
        return RedirectResponse("/password?message=saved", status_code=303)

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
            _page(
                request,
                settings=settings,
                in_fields=in_fields,
                pl_fields=pl_fields,
                in_status=in_status,
                pl_status=pl_status,
                in_error=in_error,
                pl_error=pl_error,
                env_snapshot=env_snapshot,
                mapping_complete=settings.mapping_complete,
                active="setup",
            ),
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
            _page(
                request,
                settings=settings,
                blocked=not mapping_ready(settings),
                message=message,
                error=error,
                active="match",
                year=resolve_year(year),
                years=year_choices(),
                combo=_combo_on(combo),
            ),
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

        def body(**extra: Any) -> dict[str, Any]:
            defaults: dict[str, Any] = {
                "settings": settings,
                "matches": [],
                "blocked": False,
                "year": selected_year,
                "combo": include_combos,
                "expense_count": 0,
                "document_count": 0,
                "error": "",
            }
            return _page(request, **{**defaults, **extra})

        if not mapping_ready(settings):
            return templates.TemplateResponse(
                request,
                "match_results.html",
                body(blocked=True),
            )
        aliases = alias_store(settings)
        ignores = ignore_store(settings)
        ic = in_client(settings)
        pc = pl_client(settings)
        try:
            if aliases.backfilled:
                listed, documents = await asyncio.gather(
                    cached_expenses(ic, settings, year=selected_year),
                    cached_documents(pc, settings, selected_year),
                )
            else:
                listed, documents = await asyncio.gather(
                    cached_expenses(ic, settings, year=None),
                    cached_documents(pc, settings, selected_year),
                )
                await backfill_vendor_aliases(
                    settings=settings,
                    in_client=ic,
                    pl_client=pc,
                    aliases=aliases,
                    expenses=listed,
                )
            expenses = ignores.drop_expenses(
                filter_expenses_by_year(
                    filter_unlinked_expenses(listed, settings),
                    selected_year,
                )
            )
            documents = ignores.drop_documents(
                filter_unlinked_documents(documents, settings)
            )
            matches = build_matches(
                expenses,
                documents,
                settings,
                aliases=aliases,
                include_combos=include_combos,
                lang=_lang(request),
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "match_results.html",
                body(error=str(exc)),
                status_code=500,
            )
        finally:
            await ic.aclose()
            await pc.aclose()
        return templates.TemplateResponse(
            request,
            "match_results.html",
            body(
                matches=matches,
                expense_count=len(expenses),
                document_count=len(documents),
            ),
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
            _page(
                request,
                settings=settings,
                expense=expense,
                year=selected_year,
                q=q,
                preset=preset,
                presets=PRESET_KEYS,
                unlinked_only=bool(unlinked_only),
                date_from=date_from,
                date_to=date_to,
                correspondent=correspondent,
                hits=hits,
                searched=True,
                error=error,
            ),
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
            _page(
                request,
                settings=settings,
                pairs=pairs,
                source=source,
                message=message,
                error=error,
                active="linked",
            ),
        )

    @app.get("/ignored", response_class=HTMLResponse)
    async def ignored_page(request: Request, message: str = "", error: str = "") -> HTMLResponse:
        settings = get_settings()
        ignores = ignore_store(settings)
        return templates.TemplateResponse(
            request,
            "ignored.html",
            _page(
                request,
                settings=settings,
                expense_ids=ignores.expense_ids(),
                document_ids=ignores.document_ids(),
                message=message,
                error=error,
                active="ignored",
            ),
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
            _page(
                request,
                settings=settings,
                active="queue",
                year=resolve_year(year),
                years=year_choices(),
                message=message,
                error=error,
            ),
        )

    @app.get("/queue/results", response_class=HTMLResponse)
    async def queue_results(request: Request, year: int | None = None) -> HTMLResponse:
        settings = get_settings()
        selected_year = resolve_year(year)

        def body(**extra: Any) -> dict[str, Any]:
            defaults: dict[str, Any] = {
                "settings": settings,
                "matches": [],
                "year": selected_year,
                "document_count": 0,
                "expense_count": 0,
                "tag_ok": False,
                "error": "",
            }
            return _page(request, **{**defaults, **extra})

        if not mapping_ready(settings) or not settings.pl_reverse_queue_tag:
            return templates.TemplateResponse(
                request,
                "queue_results.html",
                body(),
            )
        aliases = alias_store(settings)
        ignores = ignore_store(settings)
        ic = in_client(settings)
        pc = pl_client(settings)
        try:
            tag_id = await pc.resolve_tag_id(settings.pl_reverse_queue_tag)
            if tag_id is None:
                return templates.TemplateResponse(
                    request,
                    "queue_results.html",
                    body(),
                )
            if aliases.backfilled:
                listed, tagged = await asyncio.gather(
                    cached_expenses(ic, settings, year=selected_year),
                    cached_tagged_documents(pc, settings, tag_id, selected_year),
                )
            else:
                listed, tagged = await asyncio.gather(
                    cached_expenses(ic, settings, year=None),
                    cached_tagged_documents(pc, settings, tag_id, selected_year),
                )
                await backfill_vendor_aliases(
                    settings=settings,
                    in_client=ic,
                    pl_client=pc,
                    aliases=aliases,
                    expenses=listed,
                )
            documents = ignores.drop_documents(
                filter_unlinked_documents(tagged, settings)
            )
            expenses = ignores.drop_expenses(
                filter_expenses_by_year(
                    filter_unlinked_expenses(listed, settings),
                    selected_year,
                )
            )
            matches = build_reverse_matches(
                documents,
                expenses,
                settings,
                aliases=aliases,
                lang=_lang(request),
            )
        except Exception as exc:
            return templates.TemplateResponse(
                request,
                "queue_results.html",
                body(tag_ok=True, error=str(exc)),
                status_code=500,
            )
        finally:
            await ic.aclose()
            await pc.aclose()
        return templates.TemplateResponse(
            request,
            "queue_results.html",
            body(
                matches=matches,
                document_count=len(documents),
                expense_count=len(expenses),
                tag_ok=True,
            ),
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

    def _year_return(return_to: str, year: int, combo: str, **params: str) -> str:
        combo_q = "&combo=1" if _combo_on(combo) else ""
        extra = "".join(f"&{key}={quote(value)}" for key, value in params.items() if value)
        if return_to == "queue":
            return f"/queue?year={year}{extra}"
        if return_to == "ignored":
            return f"/ignored?{extra.lstrip('&')}" if extra else "/ignored"
        return f"/match?year={year}{combo_q}{extra}"

    @app.post("/ignore")
    async def ignore_action(
        request: Request,
        kind: Annotated[str, Form()],
        year: Annotated[int | None, Form()] = None,
        return_to: Annotated[str, Form()] = "match",
        combo: Annotated[str, Form()] = "",
        expense_id: Annotated[str, Form()] = "",
        document_id: Annotated[int | None, Form()] = None,
    ) -> RedirectResponse:
        settings = get_settings()
        selected_year = resolve_year(year)
        ignores = ignore_store(settings)
        audit = audit_log(settings)
        if kind == "expense":
            if not expense_id.strip():
                return RedirectResponse(
                    _year_return(
                        return_to, selected_year, combo, error=_tr(request, "err_ignore_id")
                    ),
                    status_code=303,
                )
            ignores.ignore_expense(expense_id)
            audit.write("ignore", expense_id=expense_id)
        elif kind == "document":
            if document_id is None:
                return RedirectResponse(
                    _year_return(
                        return_to, selected_year, combo, error=_tr(request, "err_ignore_id")
                    ),
                    status_code=303,
                )
            ignores.ignore_document(document_id)
            audit.write("ignore", document_id=document_id)
        else:
            return RedirectResponse(
                _year_return(
                    return_to, selected_year, combo, error=_tr(request, "err_ignore_kind")
                ),
                status_code=303,
            )
        return RedirectResponse(
            _year_return(
                return_to, selected_year, combo, message=_tr(request, "msg_ignored")
            ),
            status_code=303,
        )

    @app.post("/unignore")
    async def unignore_action(
        request: Request,
        kind: Annotated[str, Form()],
        expense_id: Annotated[str, Form()] = "",
        document_id: Annotated[int | None, Form()] = None,
    ) -> RedirectResponse:
        settings = get_settings()
        ignores = ignore_store(settings)
        audit = audit_log(settings)
        if kind == "expense" and expense_id.strip():
            ignores.unignore_expense(expense_id)
            audit.write("unignore", expense_id=expense_id)
        elif kind == "document" and document_id is not None:
            ignores.unignore_document(document_id)
            audit.write("unignore", document_id=document_id)
        else:
            return RedirectResponse(
                f"/ignored?error={quote(_tr(request, 'err_ignore_id'))}",
                status_code=303,
            )
        return RedirectResponse(
            f"/ignored?message={quote(_tr(request, 'msg_unignored'))}",
            status_code=303,
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

        def fail(message: str) -> RedirectResponse:
            return RedirectResponse(
                _year_return(return_to, selected_year, combo, error=message),
                status_code=303,
            )

        if not settings.mapping_complete:
            return fail(_tr(request, "err_mapping"))
        ids = _parse_document_ids(document_id, document_ids)
        if not ids:
            return fail(_tr(request, "err_no_document"))
        ic = in_client(settings)
        pc = pl_client(settings)
        aliases = alias_store(settings)
        ignores = ignore_store(settings)
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
            ignores.unignore_expense(expense_id)
            for result in results:
                ignores.unignore_document(result.document_id)
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
            invalidate_lists()
            doc_label = ", ".join(str(r.document_id) for r in results)
            msg = _tr(
                request,
                "msg_linked",
                expense=results[0].expense_number,
                docs=doc_label,
            )
            return RedirectResponse(
                _year_return(return_to, selected_year, combo, message=msg),
                status_code=303,
            )
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
            invalidate_lists()
            return RedirectResponse(
                f"/linked?message={quote(_tr(request, 'msg_unlinked'))}",
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
