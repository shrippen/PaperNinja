# PaperNinja — zentrale Designentscheidungen

Stand: 2026-08-19. Dieses Dokument hält fest, warum die App so gebaut ist,
nicht nur wie. Abweichungen sollten hier begründet werden.

Pflege: Bei jeder neuen oder geänderten Designentscheidung in derselben Änderung
aktualisieren (Cursor-Regel `.cursor/rules/design-md.mdc`). Kein Changelog —
bestehende Abschnitte überschreiben, `Stand:` auf das Tagesdatum setzen.

## Produkt

PaperNinja ist eine **Companion-App** neben Invoice Ninja und Paperless-ngx.
Sie ersetzt keines der beiden Systeme und hängt nicht als Plugin in deren UI
(Paperless hat keine UI-Plugins; Invoice-Ninja-Module erreichen die React/Flutter-UI
nicht zuverlässig).

Primärer Workflow: Ausgaben und Belege entstehen **unabhängig**. Später bietet
die App Kandidaten an; der Mensch bestätigt; Cross-Links landen in den
bestehenden Custom Fields beider Systeme.

## Architektur

- **Ein Prozess:** FastAPI + serverseitiges Jinja, HTMX **2.0.4** lokal unter
  `/static/htmx.min.js` (kein CDN; CSP verbietet fremde Scripts). Kein SPA-Build.
- **Keine Fach-Datenbank:** Matching-State kommt live aus beiden APIs. Unlinked
  heißt: Paperless-URL an der Ausgabe leer bzw. Ausgabennummer am Dokument leer.
- **Konfiguration über ENV:** Feld-Mapping (`custom_value1`–`4` bzw. Paperless
  Field-IDs) steht in `.env`, nicht in der App. Der Screen „Setup / Felder“
  zeigt nur Discovery + Copy-Hilfe; Speichern heißt Container/Prozess neu starten.
- **Minimale Persistenz in `DATA_DIR`:** App-Login (`auth.json`), gelernte
  Vendor-Aliase (`vendor_aliases.json`), Ignorieren-Liste (`ignore.json`),
  Audit-Log (`audit.log`). Das ist kein Matching-State, nur Zugangsschutz,
  Lernen, Ausblenden und Nachvollziehbarkeit.
- **Deploy:** Alpine-basiertes Docker-Image auf GHCR
  (`ghcr.io/shrippen/paperninja`), gebaut per GitHub Action (linux/amd64 +
  arm64). Compose mit `env_file` und Volume für `data/`.

```
Browser  →  PaperNinja (FastAPI)
                ├─ Invoice Ninja REST (Expenses, custom_value*)
                └─ Paperless-ngx REST (Documents, custom fields, thumb/preview)
```

API-Tokens bleiben serverseitig. Das Frontend sieht nur Proxy-Vorschauen
(`/preview/...`) unter der eigenen Session.

## Auth

- Nur **ein Passwort**, kein Benutzername (Single-Operator-Tool).
- Beim **ersten Start** ohne `auth.json`-Hash: Screen „Passwort festlegen“.
- Danach Login-Screen. Session-Cookie (HTTP-only, Signed via Starlette).
- **Rate-Limit:** Pro Fehlversuch ~0,4 s Delay (Argon2 plus Pause). Nach 5
  falschen Passwörtern (pro Client-IP, im Prozess) 60 s Lock.
  `X-Forwarded-For` nur wenn `SESSION_HTTPS=true` (Reverse-Proxy), sonst
  spoofbar.
- **Passwort ändern** unter `/password`; Hash wird in `auth.json` ersetzt und
  `auth_epoch` erhöht — andere Sessions sind danach ungültig. Die aktuelle
  Session wird neu aufgebaut (gegen Session-Fixation).
- Verify ohne gesetztes Passwort läuft gegen einen Dummy-Argon2-Hash, damit
  die Timing-Seite nicht verrät, ob schon ein Passwort existiert.
- Security-Header auf allen Antworten: `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, CSP `script-src 'self'` (HTMX lokal,
  kein CDN). Inline-Scripts in den Templates brauchen `'unsafe-inline'`.
- Das Passwort wird **nicht verschlüsselt wiederherstellbar** gespeichert,
  sondern als **Argon2id-Hash**.
- `/health` bleibt ohne Login (Docker-Healthcheck).

Passwort zurücksetzen: `auth.json` löschen (oder nur `password_hash`), App neu
starten, neues Passwort setzen.

## Matching

Human-in-the-loop, **kein Auto-Link**.

Signale (additiv, Cap 100):

| Signal         | Max | Quelle |
|----------------|-----|--------|
| Betrag         | 40  | Paperless-Betragsfeld oder OCR/Titel vs. Expense `amount` |
| Datum          | 25  | Expense-Datum vs. Doc `created`/`added`, Fenster ±N Tage |
| Vendor         | 20  | IN-Vendor vs. Correspondent/Titel (rapidfuzz); **Vendor-Aliase** aus bestätigten Links |
| Rechnungsnr.   | 30  | IN-Custom-Field/Expense-Nummer vs. PL-Feld/Titel/OCR |

Nur Kandidaten ≥ `MATCH_MIN_SCORE`. Top-N pro Ausgabe. Vorfilter: Dokumente
außerhalb ~2× Datumsfenster fliegen raus. **Ignorierte** Ausgaben und Belege
(`DATA_DIR/ignore.json`) werden vor dem Scorer entfernt — kein Auto-Link,
nur Ausblenden. Wieder einblenden unter `/ignored`. Ein späterer Link hebt
Ignore für das Paar auf.

**Vendor-Aliase:** Bei jedem bestätigten Link werden Vendor (IN) und Correspondent
(PL) normalisiert und in `vendor_aliases.json` zu Äquivalenz-Clustern zusammengeführt.
Der Scorer vergleicht dann über alle bekannten Schreibweisen. **Backfill:** Beim
ersten Match-/Queue-Lauf nach Start (Flag `backfilled` in der JSON-Datei) werden
bereits verknüpfte Expenses gelesen und dieselben Paare nachgelernt — damit Aliase
nicht nur für Links gelten, die in PaperNinja bestätigt wurden.

**1∶n Sammelbelege (opt-in):** Toggle auf `/match`, Standard **aus**. Sucht Mengen
von 2–4 unlinked Dokumenten, deren Beträge sich zur Ausgabe summieren (± Toleranz).
Pool begrenzt (Datumsfenster, max. 20 Belege mit eindeutigem Betrag). Ohne
`PL_FIELD_AMOUNT` nur Dokumente mit genau einem OCR-Betrag. Verknüpfen schreibt
die Expense-Nummer auf alle Belege und alle Paperless-URLs (leerzeichengetrennt)
ins IN-URL-Feld. Entkoppeln entfernt nur den einen Beleg, die übrigen URLs bleiben.

Warum nicht Standard: Kombinatorik plus mehr UI; der Fall ist selten; die API-Ladezeit
dominiert weiter, die Extra-CPU fällt vor allem bei vielen offenen Belegen auf.

**Jahresfilter:** Default aktuelles Kalenderjahr auf `/match` und `/queue`.
Paperless filtert mit `created__date__gte/lte`. Invoice Ninja sendet
`date=YYYY-MM-DD,YYYY-MM-DD`, wenn die API das respektiert; sonst Fallback auf
volle Liste. Client-seitig wird das Jahr immer noch gefiltert.

**Linken:** Schreibt IN (Paperless-URL + ggf. Rechnungsnummer) und Paperless
(Ausgabennummer, IN-URL, Rechnungsnummer). Scheitert Paperless, Rollback der
IN-URL best-effort. Dabei: Session-Merkliste, Audit-Zeile, Alias-Lernen.
1∶n: mehrere URLs im IN-Feld, Expense-Nummer auf jedem Beleg.

**Entkoppeln:** Leert Paperless-URL an der Ausgabe sowie Ausgabennummer und
IN-URL am Dokument (`/unlink`).

## UI / Screens

| Route | Zweck |
|-------|--------|
| `/match` | Ausgaben zuerst — Auto-Kandidaten |
| `/queue` | Belege zuerst — nur Docs mit `PL_REVERSE_QUEUE_TAG`, unlinked |
| `/linked` | Verknüpfte Paare dieser Session; sonst Fallback über `updated_at` / PL-URL |
| `/ignored` | Ausgeblendete Ausgaben/Belege; Wieder-einblenden |
| `/search` | Manuelle Paperless-Suche pro Ausgabe (HTMX-Panel) |
| `/password` | Passwort ändern |
| `/setup` | Feld-Discovery + ENV-Hilfe |

UI-Sprache: **Deutsch und Englisch** (Default `de`). Weitere Sprachen erst, wenn
DE/EN durchgängig sind.

**Manuelle Suche:** Presets (Datum ±14d, Betrag, Vendor, Rechnungsnr., Jahr,
nur unlinked), Volltext, Datumsbereich, Korrespondent-Filter. Ergebnisse mit
Vorschau und direktem Verknüpfen.

**Tastatur:** `j`/`k` zwischen Vorschlägen, `n` nächste Ausgabe/Beleg,
`Enter` verknüpfen, `s` Suche öffnen, `/` Fokus Suche/Jahr, `?` Hilfe.

**Visuelles Design:** Farben, Typografie, Icon-Stil und Landing-Page-Layout
folgen dem gemeinsamen Design-System
[shrippen/DesignDefault](https://github.com/shrippen/DesignDefault)
(Gruvbox-Dark-Palette, Rajdhani-Headings, kein Light-Mode auf Landing Pages).
App-Icon ist Variante D (Maske mit zwei Dokumenten) mit Cream-Fill (`#E8DCC4`)
auf abgerundetem Quadrat. Favicon/`apple-touch-icon` unter `/static/`;
README und GitHub-Social nutzen `docs/logo.png` bzw. `docs/social.png`.
Badges im Format `shields.io` mit `labelColor=1c1c20`, Versionswert `e8dcc4`,
Tech-Tag `83a598`, Lizenz `a89984`.

Vorschau: Miniatur über den authentifizierten Proxy (`/preview/.../thumb`);
ein Klick auf Thumbnail oder Titel öffnet die **Paperless-Detailseite**, nicht die
PDF-Proxy-URL.

## Audit-Log

Append-only `DATA_DIR/audit.log`: Zeitstempel, Aktion (`link`, `unlink`,
`ignore`, `unignore`, `password_change`), relevante IDs. Bei Überschreiten von `AUDIT_LOG_MAX_BYTES`
werden älteste Zeilen abgeschnitten (~60 % behalten). Kein Multi-User — es gibt
keinen Benutzernamen, nur die Aktion selbst.

## 1∶n-Matching

Eine Bank-/Kartenausgabe kann mehrere Belege abdecken (Sammelabbuchung). Der Matcher
schlägt dann eine **Menge** vor, deren Betragssumme zur Ausgabe passt, plus Datum
und Vendor über die Menge.

Nicht Standard, weil selten und teurer als 1:1 (Teilmengen im Datumsfenster).
UI: Checkbox „1∶n Sammelbelege“ auf `/match`. Reverse-Queue (`/queue`) bleibt 1:1
(Beleg → Ausgabe).

## Paperless Monetary-Feld

Paperless füllt Monetary-Felder in der Standard-UI **nicht** aus OCR. Ein
Post-Consume-Regex in Paperless ist **nicht automatisch zuverlässiger** als
PaperNinjas Regex: dieselbe Pattern-Qualität, aber PaperNinja kennt den
Expense-Betrag und kann unter mehreren Zahlen im OCR die passende wählen.
Ein Consume-Regex ohne Zielbetrag greift oft Zwischensumme, MwSt. oder Zeile.

Trotzdem lohnt ein **gut gemachtes** Paperless-Feld (Regex auf „Summe/Total/
Rechnungsbetrag“, nicht die erste Zahl): schnelleres Matching ohne OCR-Scan,
stabile Zahl in Paperless, und 1∶n braucht **einen** Betrag pro Beleg.

## Performance / Caching (bewusst nicht: Matching-DB)

Ladezeit kommt fast nur von den Live-APIs, nicht vom Scorer.

- **TTL-Cache im Prozess** (Default 180 s, `API_CACHE_TTL_SECONDS`; `0` = aus)
  für Expense-Listen (`in:expenses:all` / `in:expenses:{year}`) und
  Paperless-Jahreslisten (`pl:documents:{year}`, Queue-Tags). Nach erfolgreichem
  Link/Unlink werden die Keys invalidiert, damit frisch verknüpfte Items nicht
  als unlinked bleiben. Kein Matching-State auf Disk, nur RAM im Prozess.
- **Invoice-Ninja-Datumsfilter** per `date=YYYY-MM-DD,YYYY-MM-DD`. Probe einmal
  pro Prozess: HTTP 400/422 oder datierte Expenses außerhalb des Fensters →
  Filter aus, volle Liste. Undatierte Expenses zählen nicht als Leak.
  Client-seitiger Jahresfilter bleibt immer. Alias-Backfill braucht beim ersten
  Lauf die unfiltered Expense-Liste (`in:expenses:all`).
- Parallel IN+PL (`asyncio.gather`); Thumbs mit `Cache-Control` 5 min;
  `truncate_content`; Alias-Backfill nur einmal.

Weitere Optionen, falls es trotz Cache eng wird:

1. **Weniger OCR:** `PL_FIELD_AMOUNT` vermeidet Textsuche.
2. **HTTP-ETags** gegen Paperless/IN — nur wenn die APIs sie sinnvoll senden.
3. **Snapshot/SQLite** — würde die „keine Fach-DB“-Regel aufgeben; nur wenn
   Live-Reads nachweislich zu langsam sind.

## Bewusste Nicht-Ziele (aktuell)

- Dateien nach Invoice Ninja hochladen (Beleg bleibt in Paperless).
- Plugin/Fork von Paperless oder Invoice Ninja.
- Multi-User, Rollen, SSO.
- Vollautomatisches Verknüpfen ohne Bestätigung.
- Schwellen-Autolink, Erinnerungs-Mails (Backlog).

## Backlog (priorisiert)

1. **Schwellen-Autolink (opt-in)** — nur Score 95+ und eindeutiger Erstplatz.
2. **Unmatched-Erinnerung** — Zähler / n8n wenn Expenses > N Tage ohne URL.
