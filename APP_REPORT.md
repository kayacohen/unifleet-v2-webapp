# UniFleet v2 Webapp — Application Report

**Domain:** Philippine B2B fuel-voucher platform. Fleets book prepaid diesel refuels at partner gas stations; UniFleet issues branded QR vouchers; gas-station attendants redeem them. Sits between fleet customers, gas stations, ops, and a downstream fuel supplier.

## 1. Tech Stack

| Layer | Choice |
|---|---|
| Language | Python 3.11 (`.11`–`.12`) |
| Framework | Flask 3.0+ |
| WSGI | Gunicorn 21.2+ |
| Data | pandas, stdlib `sqlite3` |
| Imaging | Pillow, `qrcode`, `reportlab` (PDF) |
| Templating | Jinja2 (server-rendered, no JS framework, no build pipeline) |
| Timezones | `pytz` + `zoneinfo` (Asia/Manila) |
| Tooling | Poetry, `pyright`, `ruff` |
| Deployment | Replit → Google Cloud Run (`.replit`) |

## 2. Project Structure

```
unifleet-v2-webapp/
├── main.py               # 1213 lines — all 20 Flask routes
├── persistence.py        # CSVRepo (working) + DBRepo (incomplete)
├── models.py             # VOUCHER_COLUMNS schema, SCHEMA_SQL
├── price_store.py        # JSON price store, atomic writes
├── discount_store.py     # Thread-safe discount store + CSV audit
├── generate_voucher.py   # QR + branded PNG generation
├── report_pdf.py         # ReportLab A4-landscape supplier PDF
├── pyproject.toml        # Poetry + pyright + ruff
├── .replit               # Cloud Run deployment config
├── templates/            # 8 Jinja2 templates
├── static/               # CSS, logo, voucher template, fonts
├── data/                 # Runtime CSV/JSON (mostly gitignored)
└── archive/unused_2025-10-07/  # Archived old tools
```

## 3. Application Type

**Server-rendered Flask monolith** (single process, port 5000). No SPA, no separate API service, no queue. Mixed response types: HTML pages, public JSON APIs, token-gated supplier API, and file downloads (CSV/PDF).

## 4. Key Features & Routes (`main.py`)

| Route | Purpose |
|---|---|
| `/form` | Vouchers dashboard with search + PDF filter |
| `/book` | Main booking flow (4-letter code → station → driver/vehicle) |
| `/redeem/<vid>` | Pump-attendant "REDEEM" page |
| `/ops/voucher/<vid>/status/<s>` | Approve/redeem status transitions; triggers price math + asset gen |
| `/register` | Fleet self-registration; mints 4-letter account code |
| `/supplier-api/<vid>` | Token-gated JSON for downstream supplier |
| `/export_supplier_csv` | Supplier CSV download |
| `/supplier-sheet.pdf` | Printable A4 supplier PDF |
| `/admin/prices`, `/admin/discounts/update` | Admin UI (key-gated) |
| `/api/v1/prices`, `/api/v1/discounts`, `/api/v1/price_preview` | Public JSON APIs |
| `/healthz` | Health probe (UptimeRobot/Replit) |

**Voucher lifecycle:** `Unverified` → `Unredeemed` (QR + branded PNG generated) → `Redeemed`. `ENFORCE_PHASES=1` env flag can lock this.

## 5. Architecture

- **Single-process monolith.** All routes flat on `app` (no blueprints).
- **Repository pattern** (`persistence.py`) — `get_repo(backend)` returns `CSVRepo` (default, `data/master_vouchers.csv`) or `DBRepo` (SQLite at `data/unifleet.db`). Selected via `PERSISTENCE_BACKEND` env.
- **Sidecar stores:** `price_store` (JSON, atomic `mkstemp` + `shutil.move`), `discount_store` (JSON + `threading.Lock` + `os.replace` + CSV audit).
- **No sessions, no client state** except a `pdf_station_ids` cookie for supplier-PDF station filter.
- **Two-phase pricing:** price + discount snapshotted at booking; frozen on approval — protects customers from price drift.

## 6. Database

- **CSV mode (default):** `data/master_vouchers.csv` (utf-8-sig BOM for Excel), schema in `VOUCHER_COLUMNS` (`models.py:3-37`). Self-healing — missing columns are auto-added on write.
- **SQLite mode:** `SCHEMA_SQL` in `models.py:42-62`. **Incomplete** — `DBRepo` lacks `update_voucher_fields` and `create_unverified_booking`, so switching to DB mode breaks the booking flow.
- **Other data files:** `customers.csv` (9 fleets), `stations.csv` (10 legacy, separate from JSON price store), `ops_audit_log.csv` (49 entries), per-customer `presets/{code}_presets.csv`.
- **No migrations.**

## 7. Authentication

**None in the traditional sense.** Multiple lightweight key gates:

- `SUPPLIER_API_TOKEN` (default `"unifleet2025mvp"`) — query-string gate on `/supplier-api/<vid>` (`main.py:97`).
- `ADMIN_KEY` (default `"unifleet-admin"`) — `?key=` or `X-Admin-Key` header on admin endpoints (`main.py:98`).
- `OPS_TOKEN` — optional env-gated; empty by default, anyone with the URL can flip status (IP/UA logged).
- `app.secret_key` — hardcoded `"your_secret_key_here"`, used only for flash messages.
- `/book` accepts a 4-letter `account_code` from `customers.csv` (not a secret, just a lookup).

No CSRF, no login/logout, no sessions. Audit log captures IP + User-Agent.

## 8. Testing

**Zero tests.** No `tests/`, no `pytest`, no CI workflow, no test deps in `pyproject.toml`. `pyright` + `ruff` are configured but not a test runner.

## 9. DevOps / Deployment

- **Target:** Replit → Google Cloud Run (`.replit`). `gunicorn --bind 0.0.0.0:5000 main:app`.
- **Local dev:** Flask dev server with `debug=True` (`main.py:1211-1213`).
- **Env vars:** `SUPPLIER_API_TOKEN`, `ADMIN_KEY`, `PERSISTENCE_BACKEND`, `ENFORCE_PHASES`, `OPS_TOKEN`, `BASE_URL`. All have defaults; no `.env` file.
- **No CI/CD, no Docker, no Procfile, no `requirements.txt` fallback.**
- **Git:** 17 commits, single author (`Cohen Kaya`), 5 active branches including `main`.

## 10. Notable Patterns

- **Snapshot-on-book, freeze-on-approve** pricing (`main.py:316-411`, `667-752`).
- **Dual column-naming with auto-mirroring** — legacy and `*_php` columns kept in sync (`persistence.py:140-144`).
- **Idempotent asset generation** — `generate_assets_for_row` skips if QR/PNG already exists (`generate_oliver:251-273`).
- **Two-format voucher output:** plain `<id>.png` (dashboard) + branded `<id>_Official.png` (driver).
- **Defensive `/healthz`** — handles HEAD specially because Replit proxies strip HEAD bodies.
- **Lazy/optional `report_pdf` import** — app starts even if ReportLab missing (`main.py:21-26`).
- **Three security levels of "API":** public JSON, token-gated JSON, admin-key gated.
- **Audit log** is a flat CSV with timestamp/action/voucher_id/route/IP/UA/note (`main.py:126-154`).
- **Broken route:** `/discount-locator` (`main.py:786-793`) renders `templates/locator.html`, which **does not exist** — returns 500 on access.
- **Duplicated helpers:** `_norm_dashes` / `_slug` appear in 3+ places; could be extracted.
- **README.md is one line** (`# unifleet-v2-webapp`) — effectively empty.

## Quick File Index

| File | Role |
|---|---|
| `main.py:217-1206` | All HTTP routes |
| `persistence.py:14-255` | `CSVRepo` + incomplete `DBRepo` |
| `models.py:3-62` | Schema + SQL |
| `price_store.py:9-104` | JSON price store w/ atomic writes |
| `discount_store.py:166-213` | Thread-safe discount store |
| `generate_voucher.py:73-273` | QR + branded PNG gen |
| `report_pdf.py` | Supplier PDF builder |
| `templates/form.html` (441 LOC) | Dashboard (biggest template) |
| `templates/book.html` (353 LOC) | Booking flow |
| `templates/admin_prices.html` (231 LOC) | Admin prices UI |

**Bottom line:** Working pre-production MVP for a niche B2B fuel-discount intermediary. Solid core logic (pricing snapshots, repo abstraction, audit trail) but several rough edges: no tests, no CI, broken route, incomplete SQLite backend, hardcoded secrets with weak defaults, no real auth.
