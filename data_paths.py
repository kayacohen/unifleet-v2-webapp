"""
data_paths.py — central file-path registry for the UniFleet v2 webapp.

F2.6 of the UniFleet v2 → Railway + Postgres migration. Replaces all
hardcoded `data/...` and `static/qr_codes/...` string literals with
a single configurable root directory. On Railway this root is the
persistent Volume mounted at `/data`; in local docker-compose it is
set to `/app/data` (the host's `./data` bind mount).

Reads `UNIFLEET_DATA_DIR` from the environment. Defaults to `/data`,
matching the Railway Volume mount path. To override locally, set
the env var in `docker-compose.yml` or your shell:

  export UNIFLEET_DATA_DIR=/app/data   # local dev (bind mount)
  export UNIFLEET_DATA_DIR=/data        # Railway production

Layout
------
    $UNIFLEET_DATA_DIR/
        assets/
            qr/         QR code PNGs (one per voucher, plus _Official)
            vouchers/   full branded voucher PNGs (template + QR)
            pdfs/       reserved for future PDF outputs
        uploads/        supplier CSV uploads (unifleet_web_redemptions_input.csv)
        exports/        exported CSVs (supplier_export.csv)
        presets/        per-customer preset CSVs ({account_code}_presets.csv)
        price_history.csv       append-only price change log (Phase 1 keep)
        legacy/         read-only copies of pre-migration CSVs/JSONs
            stations.csv
            customers.csv
            requested_vouchers.csv
            master_vouchers.csv
            unifleet.db
            ops_audit_log.csv           (dead post-F2.4; kept for rollback)
            station_prices.json         (dead post-F2.3; kept for rollback)
            discount_store.json         (dead post-F2.3; kept for rollback)
            discount_history.csv        (dead post-F2.3; kept for rollback)

Static assets (read-only, baked into the image) stay under
`static/`. Logo: `static/UniFleet Logo.png`.
Template: `static/BRANDED VOUCHER TEMPLATE - UNIFLEET.png`.

Importing this module is safe at any time — it does not open DB
connections, does not call `mkdir` (use `ensure_dirs()` for that).
"""

import os
from pathlib import Path


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------

DATA_DIR: Path = Path(os.environ.get("UNIFLEET_DATA_DIR", "/data")).resolve()


# ---------------------------------------------------------------------------
# Subdirectories (all created on demand by ensure_dirs())
# ---------------------------------------------------------------------------

ASSETS_DIR: Path = DATA_DIR / "assets"
QR_DIR: Path = ASSETS_DIR / "qr"
VOUCHER_PNG_DIR: Path = ASSETS_DIR / "vouchers"
PDF_DIR: Path = ASSETS_DIR / "pdfs"

UPLOADS_DIR: Path = DATA_DIR / "uploads"
EXPORTS_DIR: Path = DATA_DIR / "exports"
PRESETS_DIR: Path = DATA_DIR / "presets"

LEGACY_DIR: Path = DATA_DIR / "legacy"


# ---------------------------------------------------------------------------
# Specific files
# ---------------------------------------------------------------------------

# Append-only audit (still in use; main.py.append_price_history)
PRICE_HISTORY_CSV: Path = DATA_DIR / "price_history.csv"

# Uploads + exports
UPLOADED_REDEMPTIONS_CSV: Path = UPLOADS_DIR / "unifleet_web_redemptions_input.csv"
SUPPLIER_EXPORT_CSV: Path = EXPORTS_DIR / "supplier_export.csv"

# Live customers register (writable). NOTE: F2.5 migrated customers
# to Postgres; this CSV is still written by the /register route as a
# Phase 2 cleanup follow-up. For now, the file lives on the volume
# so it survives container restarts and the customers table is the
# authoritative source of truth.
CUSTOMERS_CSV: Path = DATA_DIR / "customers.csv"

# Per-customer presets (read/write by main.py; one CSV per account_code)
def preset_csv_path(account_code: str) -> Path:
    """Return the path to a customer's preset CSV.

    Account codes are validated by the call sites; this helper just
    composes the path. No slugification here.
    """
    return PRESETS_DIR / f"{account_code}_presets.csv"


# Legacy CSVs/JSONs (read-only post-migration; kept for one cycle in
# case of rollback. F2.5 already migrated the data into Postgres).
LEGACY_STATIONS_CSV: Path = LEGACY_DIR / "stations.csv"
LEGACY_CUSTOMERS_CSV: Path = LEGACY_DIR / "customers.csv"
LEGACY_REQUESTED_VOUCHERS_CSV: Path = LEGACY_DIR / "requested_vouchers.csv"
LEGACY_MASTER_VOUCHERS_CSV: Path = LEGACY_DIR / "master_vouchers.csv"
LEGACY_UNIFLEET_DB: Path = LEGACY_DIR / "unifleet.db"
LEGACY_OPS_AUDIT_LOG_CSV: Path = LEGACY_DIR / "ops_audit_log.csv"
LEGACY_STATION_PRICES_JSON: Path = LEGACY_DIR / "station_prices.json"
LEGACY_DISCOUNT_STORE_JSON: Path = LEGACY_DIR / "discount_store.json"
LEGACY_DISCOUNT_HISTORY_CSV: Path = LEGACY_DIR / "discount_history.csv"


# ---------------------------------------------------------------------------
# QR helpers
# ---------------------------------------------------------------------------

def qr_png_path(voucher_id: str) -> Path:
    """Path to the bare QR code PNG for a voucher."""
    return QR_DIR / f"{voucher_id}.png"


def official_qr_png_path(voucher_id: str) -> Path:
    """Path to the branded (template + QR) PNG for a voucher."""
    return QR_DIR / f"{voucher_id}_Official.png"


# ---------------------------------------------------------------------------
# Static assets (image-baked, read-only)
# ---------------------------------------------------------------------------

# These stay under static/ and are served by Flask's default static
# handler. They are not on the volume.
STATIC_LOGO_PATH: str = "static/UniFleet Logo.png"
STATIC_VOUCHER_TEMPLATE_PATH: str = "static/BRANDED VOUCHER TEMPLATE - UNIFLEET.png"


# ---------------------------------------------------------------------------
# ensure_dirs
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    """Create all data subdirectories under DATA_DIR.

    Idempotent. Safe to call at app startup. Creates:
        ASSETS_DIR, QR_DIR, VOUCHER_PNG_DIR, PDF_DIR,
        UPLOADS_DIR, EXPORTS_DIR, PRESETS_DIR, LEGACY_DIR
    Does NOT create DATA_DIR itself (it must exist; on Railway the
    Volume mount creates it; in local dev the bind mount creates it).
    """
    for d in (
        ASSETS_DIR,
        QR_DIR,
        VOUCHER_PNG_DIR,
        PDF_DIR,
        UPLOADS_DIR,
        EXPORTS_DIR,
        PRESETS_DIR,
        LEGACY_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Flask route helper
# ---------------------------------------------------------------------------

QR_ROUTE: str = "/assets/qr"
"""URL prefix for serving QR PNGs from the volume. main.py registers
a `send_from_directory(QR_DIR, ...)` route at this prefix. Templates
should reference QRs as {{ QR_ROUTE }}/{{ voucher_id }}_Official.png.
"""
