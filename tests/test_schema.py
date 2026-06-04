"""
Tests for db/schema.sql — the 9-table F2.1 Postgres schema.

These tests assume the `schema_db` fixture has been activated for the
session, which applies db/schema.sql to a fresh test database.
"""

import psycopg
import pytest

from models import VOUCHER_COLUMNS


# ============================================================
# Helpers
# ============================================================

EXPECTED_TABLES = {
    "vouchers",
    "stations",
    "customers",
    "presets",
    "prices",
    "price_history",
    "discounts",
    "discount_history",
    "audit_log",
}

MONEY_COLUMNS = [
    "requested_amount_php",
    "live_price_php_per_liter",
    "discount_per_liter",
    "discount_total",
    "total_dispensed",
    "price_snapshot_php_per_liter",
    "discount_snapshot_php_per_liter",
    "discount_total_php",
    "total_dispensed_php",
]

TIMESTAMP_COLUMNS = [
    "transaction_date",
    "expected_refill_date",
    "redemption_timestamp",
    "created_at",
    "updated_at",
    "price_snapshot_updated_at",
    "discount_snapshot_captured_at",
    "computed_at",
]


def _columns(cur, table: str) -> dict:
    """Return {column_name: data_type} for `table`."""
    cur.execute(
        "SELECT column_name, data_type, character_maximum_length, "
        "       column_default, is_identity "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    )
    return {
        row[0]: {
            "data_type": row[1],
            "char_len": row[2],
            "default": row[3],
            "is_identity": row[4],
        }
        for row in cur.fetchall()
    }


def _primary_key(cur, table: str) -> str | None:
    cur.execute(
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = %s "
        "  AND tc.constraint_type = 'PRIMARY KEY'",
        (table,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _unique_columns(cur, table: str) -> list[tuple[str, ...]]:
    """Return list of tuples of column names, one per UNIQUE constraint on `table`."""
    cur.execute(
        "SELECT tc.constraint_name, kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = %s "
        "  AND tc.constraint_type = 'UNIQUE' "
        "ORDER BY tc.constraint_name, kcu.ordinal_position",
        (table,),
    )
    by_constraint: dict[str, list[str]] = {}
    for constraint_name, column_name in cur.fetchall():
        by_constraint.setdefault(constraint_name, []).append(column_name)
    return [tuple(cols) for cols in by_constraint.values()]


def _fk_targets(cur, table: str) -> list[tuple[str, str, str]]:
    """Return list of (from_column, to_table, to_column) for every FK on `table`."""
    cur.execute(
        "SELECT kcu.column_name, ccu.table_name, ccu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "  ON tc.constraint_name = kcu.constraint_name "
        "JOIN information_schema.constraint_column_usage ccu "
        "  ON ccu.constraint_name = tc.constraint_name "
        "WHERE tc.table_schema = 'public' "
        "  AND tc.table_name = %s "
        "  AND tc.constraint_type = 'FOREIGN KEY'",
        (table,),
    )
    return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def _indexes(cur, table: str) -> list[str]:
    """Return list of indexed column names for `table`. Strips double-quotes
    that Postgres adds around reserved words like `timestamp`."""
    cur.execute(
        "SELECT indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename = %s",
        (table,),
    )
    columns = []
    for (indexdef,) in cur.fetchall():
        # "CREATE INDEX ... ON public.vouchers USING btree (status)"
        # Pull the parenthesized column list.
        start = indexdef.find("(")
        end = indexdef.rfind(")")
        if start == -1 or end == -1:
            continue
        cols_str = indexdef[start + 1 : end]
        for col in [c.strip().strip('"') for c in cols_str.split(",")]:
            if col:
                columns.append(col)
    return columns


# ============================================================
# Table existence
# ============================================================

def test_apply_creates_all_nine_tables(schema_db):
    """All 9 expected tables exist in the public schema."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "ORDER BY table_name"
            )
            tables = {row[0] for row in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables), (
        f"Missing tables: {EXPECTED_TABLES - tables}\n"
        f"Found: {tables}"
    )


# ============================================================
# Vouchers: PK, 28 columns, default, money/timestamp types
# ============================================================

def test_vouchers_has_voucher_id_primary_key(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            assert _primary_key(cur, "vouchers") == "voucher_id"


def test_vouchers_has_all_voucher_columns(schema_db):
    """Every name in models.VOUCHER_COLUMNS exists as a column on vouchers."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    missing = [c for c in VOUCHER_COLUMNS if c not in cols]
    assert not missing, f"Missing columns on vouchers: {missing}"


def test_vouchers_status_has_default_unverified(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    assert cols["status"]["default"] and "Unverified" in cols["status"]["default"], (
        f"Expected default to contain 'Unverified', "
        f"got {cols['status']['default']!r}"
    )


def test_vouchers_money_columns_are_numeric(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    for col in MONEY_COLUMNS:
        assert cols[col]["data_type"] == "numeric", (
            f"vouchers.{col} should be numeric, got {cols[col]['data_type']!r}"
        )


def test_vouchers_timestamp_columns_are_timestamptz(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "vouchers")
    for col in TIMESTAMP_COLUMNS:
        assert cols[col]["data_type"] == "timestamp with time zone", (
            f"vouchers.{col} should be timestamptz, "
            f"got {cols[col]['data_type']!r}"
        )


# ============================================================
# Foreign keys
# ============================================================

@pytest.mark.parametrize(
    "table,from_col,to_table,to_col",
    [
        ("vouchers", "station_id", "stations", "id"),
        ("vouchers", "account_code", "customers", "account_code"),
        ("audit_log", "voucher_id", "vouchers", "voucher_id"),
        ("presets", "account_code", "customers", "account_code"),
        ("prices", "station_id", "stations", "id"),
        ("discounts", "station_id", "stations", "id"),
        ("price_history", "station_id", "stations", "id"),
        ("discount_history", "station_id", "stations", "id"),
    ],
)
def test_foreign_key(schema_db, table, from_col, to_table, to_col):
    """Each expected FK constraint exists."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            fks = _fk_targets(cur, table)
    assert (from_col, to_table, to_col) in fks, (
        f"Expected FK {table}.{from_col} -> {to_table}.{to_col}, "
        f"got {fks}"
    )


# ============================================================
# Indexes
# ============================================================

@pytest.mark.parametrize(
    "table,column",
    [
        ("vouchers", "status"),
        ("vouchers", "transaction_date"),
        ("vouchers", "created_at"),
        ("audit_log", "timestamp"),
    ],
)
def test_index_exists(schema_db, table, column):
    """Each expected non-PK index exists."""
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            indexed = _indexes(cur, table)
    assert column in indexed, (
        f"Expected index on {table}.{column}, got indexed columns: {indexed}"
    )


# ============================================================
# Stations specifics
# ============================================================

def test_stations_id_is_varchar_primary_key(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "stations")
            pk = _primary_key(cur, "stations")
    assert pk == "id", f"Expected stations PK to be 'id', got {pk!r}"
    assert cols["id"]["data_type"] == "character varying", (
        f"Expected stations.id to be varchar, got {cols['id']['data_type']!r}"
    )
    assert cols["id"]["char_len"] == 64, (
        f"Expected stations.id length 64, got {cols['id']['char_len']!r}"
    )


def test_stations_legacy_id_is_unique(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            uniques = _unique_columns(cur, "stations")
    assert ("legacy_id",) in uniques, (
        f"Expected UNIQUE on stations.legacy_id, got {uniques}"
    )


def test_stations_is_active_defaults_true(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "stations")
    assert cols["is_active"]["default"] == "true", (
        f"Expected stations.is_active default 'true', "
        f"got {cols['is_active']['default']!r}"
    )


# ============================================================
# Audit log specifics
# ============================================================

def test_audit_log_id_is_bigserial(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "audit_log")
    id_col = cols["id"]
    assert id_col["data_type"] == "bigint", (
        f"Expected audit_log.id data_type bigint, got {id_col['data_type']!r}"
    )
    assert id_col["is_identity"] == "YES", (
        f"Expected audit_log.id is_identity YES, got {id_col['is_identity']!r}"
    )


# ============================================================
# Customers specifics
# ============================================================

def test_customers_account_code_is_primary_key(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cols = _columns(cur, "customers")
            pk = _primary_key(cur, "customers")
    assert pk == "account_code", (
        f"Expected customers PK to be 'account_code', got {pk!r}"
    )
    assert cols["account_code"]["data_type"] == "character varying", (
        f"Expected customers.account_code to be varchar, "
        f"got {cols['account_code']['data_type']!r}"
    )
    assert cols["account_code"]["char_len"] == 16, (
        f"Expected customers.account_code length 16, "
        f"got {cols['account_code']['char_len']!r}"
    )


# ============================================================
# Presets specifics
# ============================================================

def test_presets_unique_on_account_code_driver_name(schema_db):
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            uniques = _unique_columns(cur, "presets")
    assert ("account_code", "driver_name") in uniques, (
        f"Expected UNIQUE(account_code, driver_name) on presets, got {uniques}"
    )


# ============================================================
# Idempotency
# ============================================================

def test_schema_apply_is_idempotent(schema_db):
    """Re-applying db/schema.sql must not error and must not drop tables."""
    import subprocess
    import sys
    from pathlib import Path

    schema_path = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
    result = subprocess.run(
        [sys.executable, "db/apply.py", str(schema_path), "--dsn", schema_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Re-apply failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cur.fetchall()}
    assert EXPECTED_TABLES.issubset(tables), (
        f"Tables lost after re-apply: {EXPECTED_TABLES - tables}"
    )
