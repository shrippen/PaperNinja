# Changelog

## Unreleased (0.1.0)

First public-ready cut. Matching stays human-in-the-loop.

- Ignore expenses and documents (`data/ignore.json`, `/ignored`); they drop out of `/match` and `/queue`. Linking clears ignore for that pair.
- UI languages: German and English only (default German). Templates and flash messages are translated.
- Login lockout after 5 failed passwords (60s) plus ~0.4s delay per failure; dummy Argon2 verify; session epoch invalidates other browsers on password change; security headers/CSP (`script-src 'self'`).
- Score factor details (amount/date/vendor/invoice) are translated with the UI language.
- HTMX 2.0.4 is served from `/static/htmx.min.js` (no CDN).
- In-process TTL cache for Invoice Ninja / Paperless lists; Invoice Ninja year filter when the API supports it.
- GitHub Actions runs `pytest` before the Docker image build.

See [DESIGN.md](DESIGN.md) for why these choices.
