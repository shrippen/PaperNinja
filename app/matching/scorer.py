from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from rapidfuzz import fuzz

from app.aliases import VendorAliasStore, normalize_name
from app.clients.invoiceninja import Expense
from app.clients.paperless import Document
from app.settings import Settings

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
            amount_source = "Paperless-Betragsfeld"
    if doc_amount is None:
        haystack = f"{document.title}\n{document.content}"
        for candidate_amount in amounts_from_text(haystack):
            if abs(candidate_amount - expense.amount) <= settings.match_amount_tolerance:
                doc_amount = candidate_amount
                amount_source = "OCR/Titel-Text"
                break
            if abs(candidate_amount - expense.amount) <= max(
                settings.match_amount_tolerance * 10, 1.0
            ):
                doc_amount = candidate_amount
                amount_source = "OCR/Titel-Text (Annäherung)"
                break

    if doc_amount is not None:
        delta = abs(doc_amount - expense.amount)
        if delta <= settings.match_amount_tolerance:
            points = AMOUNT_MAX
            detail = (
                f"Ausgabe {expense.amount:.2f} ≈ Beleg {doc_amount:.2f} "
                f"(Δ {delta:.2f}, Toleranz {settings.match_amount_tolerance:.2f}, "
                f"Quelle: {amount_source}) → +{points}"
            )
            score += points
            reasons.append(f"Betrag {doc_amount:.2f} ≈ {expense.amount:.2f}")
            factors.append(
                ScoreFactor("amount", "Betrag", points, AMOUNT_MAX, detail, True)
            )
        elif delta <= max(settings.match_amount_tolerance * 10, 1.0):
            points = 15
            detail = (
                f"Beträge nah: Ausgabe {expense.amount:.2f} vs Beleg {doc_amount:.2f} "
                f"(Δ {delta:.2f}, Quelle: {amount_source}) → +{points} "
                f"(voll nur bei Δ ≤ {settings.match_amount_tolerance:.2f})"
            )
            score += points
            reasons.append(f"Betrag nah ({doc_amount:.2f} vs {expense.amount:.2f})")
            factors.append(
                ScoreFactor("amount", "Betrag", points, AMOUNT_MAX, detail, True)
            )
        else:
            factors.append(
                ScoreFactor(
                    "amount",
                    "Betrag",
                    0,
                    AMOUNT_MAX,
                    (
                        f"Gefundener Beleg-Betrag {doc_amount:.2f} weicht zu stark von "
                        f"Ausgabe {expense.amount:.2f} ab (Δ {delta:.2f})."
                    ),
                    False,
                )
            )
    else:
        factors.append(
            ScoreFactor(
                "amount",
                "Betrag",
                0,
                AMOUNT_MAX,
                (
                    f"Kein Betrag ≈ {expense.amount:.2f} im Beleg gefunden "
                    f"(Custom Field und OCR/Titel)."
                ),
                False,
            )
        )

    # --- Date (max 25) ---
    doc_date = document.created_date or document.added
    if expense.date and doc_date:
        delta_days = abs((expense.date - doc_date).days)
        window = settings.match_date_window_days
        if delta_days <= window:
            proximity = 1 - (delta_days / max(window, 1))
            points = int(10 + 15 * proximity)
            detail = (
                f"Ausgabedatum {expense.date} vs Belegdatum {doc_date} "
                f"(Δ {delta_days} Tag(e), Fenster ±{window}) → +{points} "
                f"(näher = höher, max {DATE_MAX})"
            )
            score += points
            reasons.append(f"Datum ±{delta_days}d")
            factors.append(
                ScoreFactor("date", "Datum", points, DATE_MAX, detail, True)
            )
        elif delta_days <= window * 2:
            points = 5
            detail = (
                f"Datum nur grob passend: Ausgabe {expense.date}, Beleg {doc_date} "
                f"(Δ {delta_days}d, außerhalb ±{window}, innerhalb ±{window * 2}) → +{points}"
            )
            score += points
            reasons.append(f"Datum ±{delta_days}d (weit)")
            factors.append(
                ScoreFactor("date", "Datum", points, DATE_MAX, detail, True)
            )
        else:
            factors.append(
                ScoreFactor(
                    "date",
                    "Datum",
                    0,
                    DATE_MAX,
                    (
                        f"Daten zu weit: Ausgabe {expense.date}, Beleg {doc_date} "
                        f"(Δ {delta_days}d > ±{window * 2})."
                    ),
                    False,
                )
            )
    else:
        factors.append(
            ScoreFactor(
                "date",
                "Datum",
                0,
                DATE_MAX,
                (
                    f"Datum unvollständig (Ausgabe: {expense.date or 'fehlt'}, "
                    f"Beleg: {doc_date or 'fehlt'})."
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
                    best_against = f"Korrespondent „{corr or cname}“"
                    used_alias = vname if normalize_name(vname) != normalize_name(vendor) else ""
            if title:
                ratio = fuzz.partial_ratio(vname, title)
                if ratio >= best:
                    best = ratio
                    best_against = f"Titel „{title[:80]}“"
                    used_alias = vname if normalize_name(vname) != normalize_name(vendor) else ""
        alias_note = f" (Alias „{used_alias}“)" if used_alias else ""
        if best >= 85:
            points = VENDOR_MAX
            detail = (
                f"Vendor „{vendor}“ ≈ {best_against}{alias_note} "
                f"(Fuzzy {best}% ≥ 85%) → +{points}"
            )
            score += points
            reasons.append(f"Vendor ~{best}%")
            factors.append(
                ScoreFactor("vendor", "Vendor", points, VENDOR_MAX, detail, True)
            )
        elif best >= 60:
            points = 10
            detail = (
                f"Vendor „{vendor}“ teilweise ähnlich zu {best_against}{alias_note} "
                f"(Fuzzy {best}% ≥ 60%) → +{points}"
            )
            score += points
            reasons.append(f"Vendor ~{best}%")
            factors.append(
                ScoreFactor("vendor", "Vendor", points, VENDOR_MAX, detail, True)
            )
        else:
            factors.append(
                ScoreFactor(
                    "vendor",
                    "Vendor",
                    0,
                    VENDOR_MAX,
                    (
                        f"Vendor „{vendor}“ passt schlecht "
                        f"(bester Fuzzy {best}% gegen {best_against or 'nichts'})."
                    ),
                    False,
                )
            )
    else:
        factors.append(
            ScoreFactor(
                "vendor",
                "Vendor",
                0,
                VENDOR_MAX,
                "Ausgabe hat keinen Vendor-Namen.",
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
            invoice_hit, hit_where = needle, "Paperless-Feld Rechnungsnummer"
            break
        if needle_l in (document.title or "").lower():
            invoice_hit, hit_where = needle, "Dokumenttitel"
            break
        if needle_l in (document.content[:2000] or "").lower():
            invoice_hit, hit_where = needle, "OCR-Text"
            break

    if invoice_hit:
        points = INVOICE_MAX
        detail = (
            f"Kennung „{invoice_hit}“ gefunden in {hit_where} → +{points}"
        )
        score += points
        reasons.append(f"Rechnungsnr. „{invoice_hit}“")
        factors.append(
            ScoreFactor("invoice", "Rechnungsnr.", points, INVOICE_MAX, detail, True)
        )
    else:
        searched = ", ".join(f"„{n}“" for n in needles) or "(keine Kennung an Ausgabe)"
        factors.append(
            ScoreFactor(
                "invoice",
                "Rechnungsnr.",
                0,
                INVOICE_MAX,
                f"Keine der Kennungen {searched} in Feld/Titel/OCR gefunden.",
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
) -> ComboCandidate:
    documents = [item[0] for item in members]
    amounts = [item[1] for item in members]
    factors: list[ScoreFactor] = []
    reasons: list[str] = []
    score = 0.0
    delta = abs(sum_amount - expense.amount)
    amount_points = AMOUNT_MAX
    detail = (
        f"Summe {sum_amount:.2f} aus {len(documents)} Belegen ≈ Ausgabe "
        f"{expense.amount:.2f} (Δ {delta:.2f}) → +{amount_points}"
    )
    score += amount_points
    reasons.append(f"Summe {sum_amount:.2f} aus {len(documents)} Belegen")
    factors.append(
        ScoreFactor("amount", "Betrag (Summe)", amount_points, AMOUNT_MAX, detail, True)
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
    if date_points_list:
        date_points = int(round(sum(date_points_list) / len(date_points_list)))
        factors.append(
            ScoreFactor(
                "date",
                "Datum",
                date_points,
                DATE_MAX,
                (
                    f"Mittlere Datumsnähe der {len(documents)} Belege "
                    f"(Fenster ±{window}) → +{date_points}"
                ),
                date_points > 0,
            )
        )
        score += date_points
        if date_points:
            reasons.append("Daten im Fenster")
    else:
        factors.append(
            ScoreFactor(
                "date",
                "Datum",
                0,
                DATE_MAX,
                "Datum unvollständig für die Belegmenge.",
                False,
            )
        )

    vendor = (expense.vendor_name or "").strip()
    vendor_names = [vendor] if vendor else []
    if aliases and vendor:
        vendor_names = [n for n in aliases.equivalents(vendor) if n]
    best = 0
    best_against = ""
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
                "Vendor",
                vendor_points,
                VENDOR_MAX,
                (
                    f"Bester Vendor-Treffer in der Menge: „{vendor}“ vs "
                    f"„{best_against or '—'}“ (Fuzzy {best}%) → +{vendor_points}"
                ),
                vendor_points > 0,
            )
        )
        score += vendor_points
        if vendor_points:
            reasons.append(f"Vendor ~{best}%")
    else:
        factors.append(
            ScoreFactor(
                "vendor",
                "Vendor",
                0,
                VENDOR_MAX,
                "Ausgabe hat keinen Vendor-Namen.",
                False,
            )
        )

    factors.append(
        ScoreFactor(
            "invoice",
            "Rechnungsnr.",
            0,
            INVOICE_MAX,
            "1∶n prüft keine einzelne Rechnungsnummer (Sammelbeleg).",
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
        score_combo(expense, members, total, settings, aliases) for members, total in raw
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
) -> list[ExpenseMatch]:
    results: list[ExpenseMatch] = []
    for expense in expenses:
        ranked: list[MatchCandidate] = []
        for document in documents:
            if not document_in_window(
                expense, document, settings.match_date_window_days
            ):
                continue
            candidate = score_pair(expense, document, settings, aliases=aliases)
            if candidate.score >= settings.match_min_score:
                ranked.append(candidate)
        ranked.sort(key=lambda c: c.score, reverse=True)
        combos: list[ComboCandidate] = []
        if include_combos:
            combos = find_combo_candidates(
                expense, documents, settings, aliases=aliases
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
) -> list[DocumentMatch]:
    results: list[DocumentMatch] = []
    for document in documents:
        ranked: list[tuple[Expense, MatchCandidate]] = []
        for expense in expenses:
            if not document_in_window(
                expense, document, settings.match_date_window_days
            ):
                continue
            candidate = score_pair(expense, document, settings, aliases=aliases)
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
