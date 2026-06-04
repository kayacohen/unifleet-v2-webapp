"""One-time build-environment probe for the F1.1 Railway deploy.

Imports every heavy native dependency the application needs (Flask,
Pillow, reportlab, qrcode, pandas, pytz, psycopg) and runs a `SELECT 1`
against `$DATABASE_URL`. Exits 0 on full success, non-zero on any
failure, and prints a per-check pass/fail/skip line plus a final
`RESULT: PASS` / `RESULT: FAIL` line.

Run on Railway via `railway run python scripts/verify_build.py`.
"""
import importlib
import os
import sys

import psycopg


# (module_name, display_name) pairs. Display names match the existing
# test plan so the dep-set is locked by `test_enumerates_all_required_deps`.
DEPS = [
    ("flask", "Flask"),
    ("PIL", "Pillow"),
    ("reportlab", "reportlab"),
    ("qrcode", "qrcode"),
    ("pandas", "pandas"),
    ("pytz", "pytz"),
    ("psycopg", "psycopg"),
]

DB_TIMEOUT_SECONDS = 5


def _check_imports():
    """Import each required dep. Return True iff all passed."""
    all_ok = True
    for module_name, display_name in DEPS:
        try:
            importlib.import_module(module_name)
        except Exception as e:
            print(f"FAIL: {display_name} ({type(e).__name__}: {e})")
            all_ok = False
        else:
            print(f"PASS: {display_name}")
    return all_ok


def _check_db():
    """Connect to Postgres and run SELECT 1. Return True iff ok (or skipped)."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("SKIP: db (DATABASE_URL not set)")
        return True
    try:
        with psycopg.connect(url, connect_timeout=DB_TIMEOUT_SECONDS) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                row = cur.fetchone()
        if row and row[0] == 1:
            print("PASS: db")
            return True
        print(f"FAIL: db (unexpected SELECT 1 result: {row!r})")
        return False
    except Exception as e:
        print(f"FAIL: db ({type(e).__name__}: {e})")
        return False


def main():
    imports_ok = _check_imports()
    db_ok = _check_db()
    if imports_ok and db_ok:
        print("RESULT: PASS")
        return 0
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
