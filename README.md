# PaperNinja

Companion app that suggests matches between **Invoice Ninja expenses** and
**Paperless-ngx documents**, then writes your existing custom fields on both
sides after you confirm.

No matching database — reads and writes happen live via the two APIs. Field
mapping is ENV-only; **Setup / Felder** shows live custom fields and which
variables to set. Access is a **password-only login** (set on first start,
stored as an Argon2id hash in `data/auth.json`).

Repository: [github.com/shrippen/PaperNinja](https://github.com/shrippen/PaperNinja)  
Image: `ghcr.io/shrippen/paperninja:latest` (Alpine)  
License: [MIT](LICENSE)

## Deploy with Docker Compose

### 1. Create a directory and `.env`

```bash
mkdir paperninja && cd paperninja
curl -fsSL -o docker-compose.yml \
  https://raw.githubusercontent.com/shrippen/PaperNinja/main/docker-compose.yml
curl -fsSL -o .env.example \
  https://raw.githubusercontent.com/shrippen/PaperNinja/main/.env.example
cp .env.example .env
```

Set at least:

```env
INVOICE_NINJA_URL=https://invoicing.example.com
INVOICE_NINJA_TOKEN=
PAPERLESS_URL=https://paperless.example.com
PAPERLESS_TOKEN=
```

Behind a reverse proxy with HTTPS, also set `SESSION_HTTPS=true`.

### 2. Start

```bash
mkdir -p data
docker compose up -d
```

Open http://localhost:8080 and set the app password.

The Compose file pulls `ghcr.io/shrippen/paperninja:latest`. If the image is
not available yet, build it locally:

```bash
git clone https://github.com/shrippen/PaperNinja.git
cd PaperNinja
cp .env.example .env   # fill in tokens
docker compose up -d --build
```

### 3. Map custom fields

1. Open **Setup / Felder**
2. Copy the suggested `IN_EXPENSE_FIELD_*` / `PL_FIELD_*` lines into `.env`
3. Recreate the container: `docker compose up -d`
4. Use **Zu verknüpfen** (or **Belege** for the reverse queue)

`./data` is mounted at `/app/data` and holds `auth.json`, `vendor_aliases.json`,
and `audit.log`. Keep this volume.

### Reverse proxy

Point your proxy at `http://127.0.0.1:8080`. Example Caddy:

```caddy
paperninja.example.com {
    reverse_proxy paperninja:8080
}
```

Then set `SESSION_HTTPS=true` so the session cookie is marked Secure.

### Update

```bash
docker compose pull
docker compose up -d
```

Private GHCR package (or after the first Action run, if the package is still private):

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
```

Make the package public under GitHub → Packages → `paperninja` so `docker compose pull` works without login.

## Screens

| Screen | Route | Purpose |
|--------|-------|---------|
| Zu verknüpfen | `/match` | Unlinked expenses → suggested documents |
| Belege zuerst | `/queue` | Unlinked docs with `PL_REVERSE_QUEUE_TAG` → expenses |
| Verknüpft | `/linked` | Session links; unlink pairs |
| Dokument suchen | per expense on `/match` | Manual Paperless search with presets |
| Passwort | `/password` | Change login password |

Keyboard: `?` for help (`j`/`k`, `Enter`, `s`, `/`, `n`).

## Configuration

See [`.env.example`](.env.example). Common variables:

| Variable | Purpose |
|----------|---------|
| `INVOICE_NINJA_URL` / `INVOICE_NINJA_TOKEN` | Invoice Ninja API |
| `PAPERLESS_URL` / `PAPERLESS_TOKEN` | Paperless API token |
| `DATA_DIR` | Directory for app files (default `data`; Compose uses `/app/data`) |
| `SESSION_HTTPS` | `true` if the UI is served via HTTPS |
| `IN_EXPENSE_FIELD_INVOICE_NUMBER` | `custom_value1`–`4` |
| `IN_EXPENSE_FIELD_PAPERLESS_URL` | `custom_value1`–`4` |
| `PL_FIELD_INVOICE_NUMBER` | Paperless custom field id |
| `PL_FIELD_EXPENSE_NUMBER` | Paperless custom field id |
| `PL_FIELD_INVOICE_NINJA_URL` | Paperless custom field id |
| `PL_FIELD_AMOUNT` | Optional monetary field for matching |
| `PL_REVERSE_QUEUE_TAG` | Paperless tag name/ID for **Belege zuerst** |
| `AUDIT_LOG_MAX_BYTES` | Max size of `data/audit.log` (default 1 MiB) |
| `MATCH_DATE_WINDOW_DAYS` | Default `7` |
| `MATCH_AMOUNT_TOLERANCE` | Default `0.02` |
| `MATCH_MIN_SCORE` | Default `40` |

## Matching

Scores combine amount, date proximity, vendor/correspondent fuzzy match, and
invoice number. Only suggestions above `MATCH_MIN_SCORE` are shown. Linking is
always manual.

Optional **1∶n Sammelbelege** on `/match` (off by default) finds sets of receipts
whose amounts sum to the expense. Vendor aliases are learned from confirmed
links and backfilled once from already-linked expenses.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8080
pytest
```

## License

[MIT](LICENSE)
