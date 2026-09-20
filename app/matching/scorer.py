from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from rapidfuzz import fuzz

from app.aliases import VendorAliasStore, normalize_name
from app.clients.invoiceninja import Expense
from app.clients.paperless import Document
from app.i18n import supported_lang, t
from app.settings import Settings


def _tx(lang: str, key: str, **kwargs: object) -> str:
    return t(supported_lang(lang), key, **kwargs)

_AMOUNT_RE = re.compile(
    r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})*(?:[,.]\d{2})|\d+[,.]\d{2})(?!\d)"
)

# Score caps per signal (documented in UI)
AMOUNT_MAX = 40
DATE_MAX = 25
VENDOR_MAX = 20
INVOICE_MAX = 30


@dataclass(slots=True)
class ScoreFactor:
    key: str
    label: str
    points: int
    max_points: int
    detail: str
    matched: bool


@dataclass(slots=True)
class MatchCandidate:
    document: Document
    score: int
    reasons: list[str] = field(default_factory=list)
    factors: list[ScoreFactor] = field(default_factory=list)


@dataclass(slots=True)
class ComboCandidate:
    documents: list[Document]
    amounts: list[float]
    sum_amount: float
    score: int
    reasons: list[str] = field(default_factory=list)
    factors: list[ScoreFactor] = field(default_factory=list)


@dataclass(slots=True)
class ExpenseMatch:
    expense: Expense
    candidates: list[MatchCandidate]
    combos: list[ComboCandidate] = field(default_factory=list)


@dataclass(slots=True)
class DocumentMatch:
    document: Document
    candidates: list[tuple[Expense, MatchCandidate]]


def parse_amount(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts[-1]) == 2:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def amounts_from_text(text: str) -> list[float]:
    found: list[float] = []
    for match in _AMOUNT_RE.finditer(text or ""):
        parsed = parse_amount(match.group(1))
        if parsed is not None:
            found.append(parsed)
    return found


def score_pair(
    expense: Expense,
    document: Document,
    settings: Settings,
    aliases: VendorAliasStore | None = None,
    lang: str = "de",
) -> MatchCandidate:
    factors: list[ScoreFactor] = []
    reasons: list[str] = []
    score = 0.0

    # --- Amount (max 40) ---
    doc_amount = None
    amount_source = ""
    if settings.pl_field_amount is not None:
        doc_amount = parse_amount(document.custom_value(settings.pl_field_amount))
        if doc_amount is not None:
            amount_source = _tx(lang, "score_src_field")
    if doc_amount is None:
        haystack = f"{document.title}\n{document.content}"
        for candidate_amount in amounts_from_text(haystack):
            if abs(candidate_amount - expense.amount) <= settings.match_amount_tolerance:
                doc_amount = candidate_amount
                amount_source = _tx(lang, "score_src_ocr")
                break
            if abs(candidate_amount - expense.amount) <= max(
                settings.match_amount_tolerance * 10, 1.0
            ):
                doc_amount = candidate_amount
                amount_source = _tx(lang, "score_src_ocr_approx")
                break

    amount_label = _tx(lang, "factor_amount")
    if doc_amount is not None:
        delta = abs(doc_amount - expense.amount)
        if delta <= settings.match_amount_tolerance:
            points = AMOUNT_MAX
            detail = _tx(
                lang,
                "score_amount_exact",
                expense=f"{expense.amount:.2f}",
                doc=f"{doc_amount:.2f}",
                delta=f"{delta:.2f}",
                tol=f"{settings.match_amount_tolerance:.2f}",
                source=amount_source,
                points=points,
            )
            score += points
            reasons.append(
                _tx(
                    lang,
                    "score_reason_amount",
                    doc=f"{doc_amount:.2f}",
                    expense=f"{expense.amount:.2f}",
                )
            )
            factors.append(
                ScoreFactor("amount", amount_label, points, AMOUNT_MAX, detail, True)
            )
        elif delta <= max(settings.match_amount_tolerance * 10, 1.0):
            points = 15
            detail = _tx(
                lang,
                "score_amount_near",
                expense=f"{expense.amount:.2f}",
                doc=f"{doc_amount:.2f}",
                delta=f"{delta:.2f}",
                source=amount_source,
                points=points,
                tol=f"{settings.match_amount_tolerance:.2f}",
            )
            score += points
            reasons.append(
                _tx(
                    lang,
                    "score_reason_amount_near",
                    doc=f"{doc_amount:.2f}",
                    expense=f"{expense.amount:.2f}",
                )
            )
            factors.append(
                ScoreFactor("amount", amount_label, points, AMOUNT_MAX, detail, True)
            )
        else:
            factors.append(
                ScoreFactor(
                    "amount",
                    amount_label,
                    0,
                    AMOUNT_MAX,
                    _tx(
                        lang,
                        "score_amount_far",
                        doc=f"{doc_amount:.2f}",
                        expense=f"{expense.amount:.2f}",
                        delta=f"{delta:.2f}",
                    ),
                    False,
                )
            )
    else:
        factors.append(
            ScoreFactor(
                "amount",
                amount_label,
                0,
                AMOUNT_MAX,
                _tx(lang, "score_amount_missing", expense=f"{expense.amount:.2f}"),
                False,
            )
        )

    # --- Date (max 25) ---
    doc_date = document.created_date or document.added
    date_label = _tx(lang, "factor_date")
    missing = _tx(lang, "score_missing")
    if expense.date and doc_date:
        delta_days = abs((expense.date - doc_date).days)
        window = settings.match_date_window_days
        if delta_days <= window:
            proximity = 1 - (delta_days / max(window, 1))
            points = int(10 + 15 * proximity)
            detail = _tx(
                lang,
                "score_date_close",
                expense=expense.date,
                doc=doc_date,
                days=delta_days,
                window=window,
                points=points,
                max=DATE_MAX,
            )
            score += points
            reasons.append(_tx(lang, "score_reason_date", days=delta_days))
            factors.append(
                ScoreFactor("date", date_label, points, DATE_MAX, detail, True)
            )
        elif delta_days <= window * 2:
            points = 5
            detail = _tx(
                lang,
                "score_date_wide",
                expense=expense.date,
                doc=doc_date,
                days=delta_days,
                window=window,
                outer=window * 2,
                points=points,
            )
            score += points
            reasons.append(_tx(lang, "score_reason_date_wide", days=delta_days))
            factors.append(
                ScoreFactor("date", date_label, points, DATE_MAX, detail, True)
            )
        else:
            factors.append(
                ScoreFactor(
                    "date",
                    date_label,
                    0,
                    DATE_MAX,
                    _tx(
                        lang,
                        "score_date_far",
                        expense=expense.date,
                        doc=doc_date,
                        days=delta_days,
                        outer=window * 2,
                    ),
                    False,
                )
            )
    else:
        factors.append(
            ScoreFactor(
                "date",
                date_label,
                0,
                DATE_MAX,
                _tx(
                    lang,
                    "score_date_incomplete",
                    expense=expense.date or missing,
                    doc=doc_date or missing,
                ),
                False,
            )
        )

    # --- Vendor / correspondent (max 20) ---
    vendor = (expense.vendor_name or "").strip()
    corr = (document.correspondent_name or "").strip()
    title = (document.title or "").strip()
    vendor_names = [vendor] if vendor else []
    if aliases and vendor:
        vendor_names = [n for n in aliases.equivalents(vendor) if n]
    if vendor:
        best = 0
        best_against = ""
        used_alias = ""
        corr_names = [corr] if corr else []
        if aliases and corr:
            corr_names = list(aliases.equivalents(corr)) or corr_names
        for vname in vendor_names:
            for cname in corr_names:
                if not cname:
                    continue
                ratio = fuzz.token_set_ratio(vname, cname)
                if ratio >= best:
                    best = ratio
                    best_against = _tx(lang, "score_against_correspondent", name=corr or cname)
                    used_alias = vname if normalize_name(vname) != normalize_name(vendor) else ""
            if title:
                ratio = fuzz.partial_ratio(vname, title)
                if ratio >= best:
                    best = ratio
                    best_against = _tx(lang, "score_against_title", name=title[:80])
                    used_alias = vname if normalize_name(vname) != normalize_name(vendor) else ""
        alias_note = _tx(lang, "score_alias_note", name=used_alias) if used_alias else ""
        vendor_label = _tx(lang, "factor_vendor")
        if best >= 85:
            points = VENDOR_MAX
            detail = _tx(
                lang,
                "score_vendor_strong",
                vendor=vendor,
                against=best_against,
                alias=alias_note,
                best=best,
                points=points,
            )
            score += points
            reasons.append(_tx(lang, "score_reason_vendor", best=best))
            factors.append(
                ScoreFactor("vendor", vendor_label, points, VENDOR_MAX, detail, True)
            )
        elif best >= 60:
            points = 10
            detail = _tx(
                lang,
                "score_vendor_partial",
                vendor=vendor,
                against=best_against,
                alias=alias_note,
                best=best,
                points=points,
            )
            score += points
            reasons.append(_tx(lang, "score_reason_vendor", best=best))
            factors.append(
                ScoreFactor("vendor", vendor_label, points, VENDOR_MAX, detail, True)
            )
        else:
            factors.append(
                ScoreFactor(
                    "vendor",
                    vendor_label,
                    0,
                    VENDOR_MAX,
                    _tx(
                        lang,
                        "score_vendor_weak",
                        vendor=vendor,
                        best=best,
                        against=best_against or _tx(lang, "score_against_none"),
                    ),
                    False,
                )
            )
    else:
        factors.append(
            ScoreFactor(
                "vendor",
                _tx(lang, "factor_vendor"),
                0,
                VENDOR_MAX,
                _tx(lang, "score_vendor_missing"),
                False,
            )
        )

    # --- Invoice number (max 30) ---
    inv_slot = settings.in_expense_field_invoice_number
    expense_inv = expense.custom(inv_slot) if inv_slot else ""
    pl_inv = ""
    if settings.pl_field_invoice_number is not None:
        raw = document.custom_value(settings.pl_field_invoice_number)
        pl_inv = str(raw or "").strip()

    needles = [n for n in (expense_inv, expense.number) if n]
    hay_parts = [pl_inv, document.title, document.content[:2000]]
    hay = " ".join(filter(None, hay_parts))
    invoice_hit = None
    hit_where = ""
    for needle in needles:
        if len(needle) < 3:
            continue
        needle_l = needle.lower()
        if pl_inv and needle_l in pl_inv.lower():
            invoice_hit, hit_where = needle, _tx(lang, "score_invoice_where_field")
            break
        if needle_l in (document.title or "").lower():
            invoice_hit, hit_where = needle, _tx(lang, "score_invoice_where_title")
            break
        if needle_l in (document.content[:2000] or "").lower():
            invoice_hit, hit_where = needle, _tx(lang, "score_invoice_where_ocr")
            break

    invoice_label = _tx(lang, "factor_invoice")
    if invoice_hit:
        points = INVOICE_MAX
        detail = _tx(
            lang,
            "score_invoice_hit",
            needle=invoice_hit,
            where=hit_where,
            points=points,
        )
        score += points
        reasons.append(_tx(lang, "score_reason_invoice", needle=invoice_hit))
        factors.append(
            ScoreFactor("invoice", invoice_label, points, INVOICE_MAX, detail, True)
        )
    else:
        searched = ", ".join(needles) or _tx(lang, "score_invoice_none")
        factors.append(
            ScoreFactor(
                "invoice",
                invoice_label,
                0,
                INVOICE_MAX,
                _tx(lang, "score_invoice_miss", searched=searched),
                False,
            )
        )

    capped = int(min(100, round(score)))
    return MatchCandidate(
        document=document,
        score=capped,
        reasons=reasons,
        factors=factors,
    )


def document_in_window(
    expense: Expense,
    document: Document,
    window_days: int,
) -> bool:
    if not expense.date:
        return True
    doc_date = document.created_date or document.added
    if not doc_date:
        return True
    return abs((expense.date - doc_date).days) <= window_days * 2


COMBO_MAX_SIZE = 4
COMBO_POOL = 20
COMBO_TOP_N = 3


def unique_amounts(values: list[float]) -> list[float]:
    out: list[float] = []
    seen: set[float] = set()
    for value in values:
        key = round(value, 2)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def combo_amount(document: Document, settings: Settings) -> float | None:
    """One amount per document for 1:n. Prefer the monetary field; OCR only if unambiguous."""
    if settings.pl_field_amount is not None:
        parsed = parse_amount(document.custom_value(settings.pl_field_amount))
        if parsed is not None:
            return parsed
    found = unique_amounts(
        amounts_from_text(f"{document.title}\n{document.content}")
    )
    if len(found) == 1:
        return found[0]
    return None


def score_combo(
    expense: Expense,
    members: list[tuple[Document, float]],
    sum_amount: float,
    settings: Settings,
    aliases: VendorAliasStore | None = None,
    lang: str = "de",
) -> ComboCandidate:
    documents = [item[0] for item in members]
    amounts = [item[1] for item in members]
    factors: list[ScoreFactor] = []
    reasons: list[str] = []
    score = 0.0
    delta = abs(sum_amount - expense.amount)
    amount_points = AMOUNT_MAX
    detail = _tx(
        lang,
        "score_combo_amount",
        sum=f"{sum_amount:.2f}",
        count=len(documents),
        expense=f"{expense.amount:.2f}",
        delta=f"{delta:.2f}",
        points=amount_points,
    )
    score += amount_points
    reasons.append(
        _tx(
            lang,
            "score_reason_combo_amount",
            sum=f"{sum_amount:.2f}",
            count=len(documents),
        )
    )
    factors.append(
        ScoreFactor(
            "amount",
            _tx(lang, "factor_amount_sum"),
            amount_points,
            AMOUNT_MAX,
            detail,
            True,
        )
    )

    window = settings.match_date_window_days
    date_points_list: list[int] = []
    for document in documents:
        doc_date = document.created_date or document.added
        if expense.date and doc_date:
            delta_days = abs((expense.date - doc_date).days)
            if delta_days <= window:
                proximity = 1 - (delta_days / max(window, 1))
                date_points_list.append(int(10 + 15 * proximity))
            elif delta_days <= window * 2:
                date_points_list.append(5)
            else:
                date_points_list.append(0)
    date_label = _tx(lang, "factor_date")
    if date_points_list:
        date_points = int(round(sum(date_points_list) / len(date_points_list)))
        factors.append(
            ScoreFactor(
                "date",
                date_label,
                date_points,
                DATE_MAX,
                _tx(
                    lang,
                    "score_combo_date",
                    count=len(documents),
                    window=window,
                    points=date_points,
                ),
                date_points > 0,
            )
        )
        score += date_points
        if date_points:
            reasons.append(_tx(lang, "score_reason_combo_dates"))
    else:
        factors.append(
            ScoreFactor(
                "date",
                date_label,
                0,
                DATE_MAX,
                _tx(lang, "score_combo_date_missing"),
                False,
            )
        )

    vendor = (expense.vendor_name or "").strip()
    vendor_names = [vendor] if vendor else []
    if aliases and vendor:
        vendor_names = [n for n in aliases.equivalents(vendor) if n]
    best = 0
    best_against = ""
    vendor_label = _tx(lang, "factor_vendor")
    if vendor:
        for document in documents:
            corr = (document.correspondent_name or "").strip()
            corr_names = [corr] if corr else []
            if aliases and corr:
                corr_names = list(aliases.equivalents(corr)) or corr_names
            for vname in vendor_names:
                for cname in corr_names:
                    if not cname:
                        continue
                    ratio = fuzz.token_set_ratio(vname, cname)
                    if ratio >= best:
                        best = ratio
                        best_against = corr or cname
                title = document.title or ""
                if title:
                    ratio = fuzz.partial_ratio(vname, title)
                    if ratio >= best:
                        best = ratio
                        best_against = title[:80]
        if best >= 85:
            vendor_points = VENDOR_MAX
        elif best >= 60:
            vendor_points = 10
        else:
            vendor_points = 0
        factors.append(
            ScoreFactor(
                "vendor",
                vendor_label,
                vendor_points,
                VENDOR_MAX,
                _tx(
                    lang,
                    "score_combo_vendor",
                    vendor=vendor,
                    against=best_against or "—",
                    best=best,
                    points=vendor_points,
                ),
                vendor_points > 0,
            )
        )
        score += vendor_points
        if vendor_points:
            reasons.append(_tx(lang, "score_reason_vendor", best=best))
    else:
        factors.append(
            ScoreFactor(
                "vendor",
                vendor_label,
                0,
                VENDOR_MAX,
                _tx(lang, "score_vendor_missing"),
                False,
            )
        )

    factors.append(
        ScoreFactor(
            "invoice",
            _tx(lang, "factor_invoice"),
            0,
            INVOICE_MAX,
            _tx(lang, "score_combo_invoice"),
            False,
        )
    )
    capped = int(min(100, round(score)))
    return ComboCandidate(
        documents=documents,
        amounts=amounts,
        sum_amount=sum_amount,
        score=capped,
        reasons=reasons,
        factors=factors,
    )


def find_combo_candidates(
    expense: Expense,
    documents: list[Document],
    settings: Settings,
    aliases: VendorAliasStore | None = None,
    lang: str = "de",
) -> list[ComboCandidate]:
    target = expense.amount
    tol = settings.match_amount_tolerance
    pool: list[tuple[Document, float]] = []
    for document in documents:
        if not document_in_window(expense, document, settings.match_date_window_days):
            continue
        amount = combo_amount(document, settings)
        if amount is None or amount <= 0:
            continue
        if abs(amount - target) <= tol:
            continue
        if amount > target + tol:
            continue
        pool.append((document, amount))
    pool.sort(key=lambda item: item[1], reverse=True)
    pool = pool[:COMBO_POOL]

    raw: list[tuple[list[tuple[Document, float]], float]] = []

    def dfs(start: int, chosen: list[tuple[Document, float]], total: float) -> None:
        if len(raw) >= 24:
            return
        if len(chosen) >= 2 and abs(total - target) <= tol:
            raw.append((list(chosen), total))
            return
        if len(chosen) >= COMBO_MAX_SIZE:
            return
        for idx in range(start, len(pool)):
            _document, amount = pool[idx]
            nxt = total + amount
            if nxt > target + tol:
                continue
            chosen.append(pool[idx])
            dfs(idx + 1, chosen, nxt)
            chosen.pop()

    dfs(0, [], 0.0)
    ranked = [
        score_combo(expense, members, total, settings, aliases, lang=lang)
        for members, total in raw
    ]
    ranked = [item for item in ranked if item.score >= settings.match_min_score]
    ranked.sort(key=lambda item: (-item.score, len(item.documents)))
    return ranked[:COMBO_TOP_N]


def build_matches(
    expenses: list[Expense],
    documents: list[Document],
    settings: Settings,
    aliases: VendorAliasStore | None = None,
    include_combos: bool = False,
    lang: str = "de",
) -> list[ExpenseMatch]:
    results: list[ExpenseMatch] = []
    for expense in expenses:
        ranked: list[MatchCandidate] = []
        for document in documents:
            if not document_in_window(
                expense, document, settings.match_date_window_days
            ):
                continue
            candidate = score_pair(
                expense, document, settings, aliases=aliases, lang=lang
            )
            if candidate.score >= settings.match_min_score:
                ranked.append(candidate)
        ranked.sort(key=lambda c: c.score, reverse=True)
        combos: list[ComboCandidate] = []
        if include_combos:
            combos = find_combo_candidates(
                expense, documents, settings, aliases=aliases, lang=lang
            )
        results.append(
            ExpenseMatch(
                expense=expense,
                candidates=ranked[: settings.match_top_n],
                combos=combos,
            )
        )
    results.sort(
        key=lambda m: (
            m.combos[0].score if m.combos else -1,
            m.candidates[0].score if m.candidates else -1,
        ),
        reverse=True,
    )
    return results


def build_reverse_matches(
    documents: list[Document],
    expenses: list[Expense],
    settings: Settings,
    aliases: VendorAliasStore | None = None,
    lang: str = "de",
) -> list[DocumentMatch]:
    results: list[DocumentMatch] = []
    for document in documents:
        ranked: list[tuple[Expense, MatchCandidate]] = []
        for expense in expenses:
            if not document_in_window(
                expense, document, settings.match_date_window_days
            ):
                continue
            candidate = score_pair(
                expense, document, settings, aliases=aliases, lang=lang
            )
            if candidate.score >= settings.match_min_score:
                ranked.append((expense, candidate))
        ranked.sort(key=lambda pair: pair[1].score, reverse=True)
        results.append(
            DocumentMatch(
                document=document,
                candidates=ranked[: settings.match_top_n],
            )
        )
    results.sort(
        key=lambda m: (m.candidates[0][1].score if m.candidates else -1),
        reverse=True,
    )
    return results


def filter_unlinked_expenses(
    expenses: list[Expense],
    settings: Settings,
) -> list[Expense]:
    slot = settings.in_expense_field_paperless_url
    if not slot:
        return expenses
    return [e for e in expenses if not (e.custom(slot) or "").strip()]


def filter_expenses_by_year(expenses: list[Expense], year: int) -> list[Expense]:
    out: list[Expense] = []
    for expense in expenses:
        if expense.date is None:
            continue
        if expense.date.year == year:
            out.append(expense)
    return out


def filter_unlinked_documents(
    documents: list[Document],
    settings: Settings,
) -> list[Document]:
    field_id = settings.pl_field_expense_number
    if field_id is None:
        return documents
    out: list[Document] = []
    for doc in documents:
        value = doc.custom_value(field_id)
        if value is None or str(value).strip() == "":
            out.append(doc)
    return out


def year_choices(current: date | None = None, span: int = 6) -> list[int]:
    today = current or date.today()
    return list(range(today.year, today.year - span, -1))


def date_range_label(window_days: int) -> str:
    return f"±{window_days} Tage"


def within_days(d: date | None, center: date | None, days: int) -> bool:
    if not d or not center:
        return True
    return abs((d - center).days) <= days


def expand_window(center: date, days: int) -> tuple[date, date]:
    delta = timedelta(days=days)
    return center - delta, center + delta
