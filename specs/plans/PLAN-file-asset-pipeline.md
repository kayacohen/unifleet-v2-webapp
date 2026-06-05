# F2.6 — File Asset Pipeline on Railway Volume

> Replaces hardcoded `data/...` and `static/qr_codes/...` paths
> with a single configurable root directory (`UNIFLEET_DATA_DIR`).
> On Railway this is the persistent Volume at `/data`; in local
> docker-compose it defaults to `/app/data` (the host's `./data`
> bind mount). Generated assets (QR PNGs, voucher PNGs, presets,
> exports, uploads, price history) all live on the volume.

**Feature spec:** PROJECT-migrate-to-railway.md §Feature Map row F2.6
**Depends on:** F2.5 (data already in PG; only file assets remain)
**Status:** done

## Audit of file writes (pre-F2.6)

| Path | Written by | Status pre-F2.6 |
|------|------------|-----------------|
| `static/qr_codes/{vid}.png` | `generate_voucher.generate_qr_image` | Lost on container restart |
| `static/qr_codes/{vid}_Official.png` | `generate_voucher.generate_branded_image` | Lost on container restart |
| `data/presets/{code}_presets.csv` | `main.book` | Lost on container restart |
| `data/unifleet_web_redemptions_input.csv` | `main.upload_csv` | Lost on container restart |
| `data/supplier_export.csv` | `main.supplier_export` | Lost on container restart |
| `data/price_history.csv` | `main.append_price_history` | Lost on container restart |
| `data/customers.csv` | `main.register` | Lost on container restart |
| `data/master_vouchers.csv` | `persistence.CSVRepo._write` | Lost on container restart |
| `data/unifleet.db` | `persistence.DBRepo.__init__` | Lost on container restart |
| `data/ops_audit_log.csv` | ~~`main.append_audit`~~ (F2.4 dead) | — |
| `data/discount_store.json` | (no longer written, F2.3) | — |
| `data/discount_history.csv` | (no longer written, F2.3) | — |

All 8 active writes go to a single root directory (the volume).

## New module: `data_paths.py`

~120 lines, no DB connections, no I/O on import.

Layout (under `$UNIFLEET_DATA_DIR`):
```
assets/
    qr/             QR code PNGs (one per voucher, plus _Official)
    vouchers/       full branded voucher PNGs (template + QR)
    pdfs/           reserved for future PDF outputs
uploads/            supplier CSV uploads
exports/            exported CSVs (supplier_export.csv)
presets/            per-customer preset CSVs
price_history.csv   append-only price change log
legacy/             read-only copies of pre-migration CSVs/JSONs
    stations.csv, customers.csv, requested_vouchers.csv,
    master_vouchers.csv, unifleet.db, ops_audit_log.csv,
    station_prices.json, discount_store.json,
    discount_history.csv
```

Static assets (baked into the image, not on the volume) stay under
`static/` and are served by Flask's default static handler:
- `static/UniFleet Logo.png` (logo)
- `static/BRANDED VOUCHER TEMPLATE - UNIFLEET.png` (voucher template)

### Helpers

- `DATA_DIR`: `Path` resolved from `$UNIFLEET_DATA_DIR` (default `/data`)
- `ensure_dirs()`: creates the full subdirectory tree (idempotent)
- `qr_png_path(vid)`, `official_qr_png_path(vid)`: QR PNG paths
- `preset_csv_path(code)`: preset CSV path
- `QR_ROUTE = "/assets/qr"`: URL prefix for Flask route

## Changes

### New file
- `data_paths.py` (~120 lines): central path registry
- `tests/test_data_paths.py` (10 tests): unit tests for the module

### Modified files
- `main.py`:
  - `import data_paths; data_paths.ensure_dirs()` at startup
  - `UPLOAD_FOLDER` → `str(data_paths.UPLOADS_DIR)`
  - `PRICE_HISTORY_PATH` → `str(data_paths.PRICE_HISTORY_CSV)`
  - 5× `preset_path` → `data_paths.preset_csv_path(account_code)`
  - `/book` reads `data_paths.LEGACY_REQUESTED_VOUCHERS_CSV` for the
    booking form's CSV download link
  - `/register` writes `data_paths.CUSTOMERS_CSV`
  - `/discount-locator` reads `data_paths.LEGACY_STATIONS_CSV`
  - `/supplier-export` writes `data_paths.SUPPLIER_EXPORT_CSV`
  - `/upload_csv` saves to `data_paths.UPLOADED_REDEMPTIONS_CSV`
  - QR existence checks use `data_paths.qr_png_path()` /
    `data_paths.official_qr_png_path()`
  - `/delete_png` deletes via `data_paths.qr_png_path()` /
    `data_paths.official_qr_png_path()`
  - `/supplier-sheet.pdf` uses `data_paths.STATIC_LOGO_PATH`
  - New route: `@app.route("/assets/qr/<path:filename>")` →
    `send_from_directory(QR_DIR, filename)`
- `generate_voucher.py`:
  - `QR_OUTPUT_DIR = str(data_paths.QR_DIR) + "/"`
  - `LOGO_PATH` / `TEMPLATE_PATH` from `data_paths.STATIC_*`
  - `MASTER_VOUCHERS = str(data_paths.LEGACY_MASTER_VOUCHERS_CSV)`
  - `upload_path = str(data_paths.UPLOADED_REDEMPTIONS_CSV)`
- `persistence.py`:
  - `MASTER_CSV` / `SQLITE_PATH` from `data_paths.LEGACY_*`
  - `_ensure_dirs()` delegates to `data_paths.ensure_dirs()`
- `models.py`:
  - `SQLITE_PATH = str(data_paths.LEGACY_UNIFLEET_DB)`
- `discount_store.py`:
  - `DEFAULT_JSON_PATH` / `DEFAULT_HISTORY_CSV_PATH` from
    `data_paths.LEGACY_DISCOUNT_*` (back-compat constants only,
    no longer written at runtime)
- `templates/form.html`:
  - QR download link `/static/qr_codes/...` → `/assets/qr/...`
- `templates/admin_prices.html`:
  - UI text updated: "Pre-DB JSON store" → "Postgres-backed"
- `audit_log.py`:
  - Docstring comment updated to reference
    `data_paths.LEGACY_OPS_AUDIT_LOG_CSV`
- `docker-compose.yml`:
  - New env: `UNIFLEET_DATA_DIR: ${UNIFLEET_DATA_DIR:-/app/data}`
  - Existing `./data:/app/data` bind mount unchanged (now matches
    the env var default for local dev)

## Tasks

### T1 — `data_paths.py` + update all writes + new QR route
> **Status:** done
> **Effort:** m
> **Priority:** high
> **Depends on:** F2.5

Centralize every file path. Add the `/assets/qr/<path>` Flask
route to serve QRs from the volume. Update all writes to use the
new paths. New module: `data_paths.py` with 10 unit tests.

**Deliverables:**
- `data_paths.py` (new, ~120 lines)
- `tests/test_data_paths.py` (new, 10 tests)
- `main.py` updated (~12 path references replaced + 1 new route)
- `generate_voucher.py` updated (4 path references)
- `persistence.py` updated (2 path references)
- `models.py` updated (1 path reference)
- `discount_store.py` updated (2 back-compat constants)
- `templates/form.html` updated (1 QR URL)
- `templates/admin_prices.html` updated (UI text)
- `audit_log.py` updated (1 docstring)
- `docker-compose.yml` updated (1 new env var)

### T2 — plan doc + commit + push
> **Status:** done
> **Effort:** s
> **Priority:** medium

Write the F2.6 plan doc and commit + push.

## Verification

- **Test suite**: 107/107 pass (~14s)
  - 10 new `test_data_paths.py` tests
  - 97 pre-existing tests, no regressions
- **Import smoke**: `data_paths`, `main`, `generate_voucher`,
  `persistence` all import cleanly
- **Path resolution smoke**:
  - `main.UPLOAD_FOLDER` = `/app/data/uploads`
  - `main.PRICE_HISTORY_PATH` = `/app/data/price_history.csv`
  - `generate_voucher.QR_OUTPUT_DIR` = `/app/data/assets/qr/`
  - `persistence.MASTER_CSV` = `/app/data/legacy/master_vouchers.csv`
- **End-to-end smoke**:
  - Wrote test QR PNGs to `/app/data/assets/qr/`
  - Wrote test preset CSV to `/app/data/presets/`
  - Wrote price history to `/app/data/price_history.csv`
  - `GET /assets/qr/UF-CURL-001.png` → 200 image/png (287 bytes)
  - `GET /assets/qr/nonexistent.png` → 404
- **Real HTTP test**: QR route serves PNGs over the Flask test
  client with the right content-type and size

## Known behavior change

- **QR download URL changed**: `/static/qr_codes/{vid}.png` →
  `/assets/qr/{vid}.png`. Templates updated; no call site
  references the old path.
- **Customers CSV write path changed**: `data/customers.csv` →
  `/app/data/customers.csv` (or `/data/customers.csv` on Railway).
  The register flow still writes a CSV; this is by design — the
  CSV is the live source of truth for `/register` and `/book`
  regardless of `PERSISTENCE_BACKEND`. The Postgres `customers`
  table (populated by F2.5) is a backfilled snapshot used for
  query/report purposes; the CSV continues to receive new
  registrations.
- **`/data` defaults to `/data`**, not `./data`. Local docker
  sets `UNIFLEET_DATA_DIR=/app/data` to preserve the existing
  bind-mount experience. Bare-host tests work either way because
  the path is relative to the volume or to a tmp dir.

## Open follow-ups

- **Volume backup strategy** on Railway (out of scope for F2.6)
- **PDF dir reserved** for future on-disk PDF caching
  (`assets/pdfs/`); not used yet
- **NOT in scope (per user direction)**:
  - Do NOT delete `data/*.csv|json` files (CSV mode stays supported)
  - Do NOT drop `CSVRepo` or `DBRepo` from `persistence.py`
  - Do NOT change `PERSISTENCE_BACKEND` default from `'csv'` to `'pg'`
  - The CSV/DB backends remain first-class supported options

## Deployment notes

Railway service `web` env (set in Railway dashboard or
`railway.toml`):

```
UNIFLEET_DATA_DIR=/data
```

The Railway Volume `data` must be mounted at `/data` (per
PROJECT-migrate-to-railway.md §Volumes). After F2.6 is merged,
`make test-db` in CI / on a fresh host will need a one-time
`scripts/migrate_to_postgres.py` run to backfill the legacy
files into the new layout (or they can be initialized empty
on a greenfield deployment).
