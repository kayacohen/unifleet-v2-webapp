"""
db/postgres_repo.py — PostgresRepo class.

F2.2 of the UniFleet v2 → Railway + Postgres migration. Implements
the full Repo interface from persistence.py against the F2.1 schema,
with a connection pool (psycopg_pool) for concurrent access.

Usage:
    repo = PostgresRepo(dsn="postgresql://user:pass@host/db")
    try:
        repo.append_vouchers([{...}])
        rows = repo.list_recent_vouchers(limit=50)
    finally:
        repo.close()

The DSN can also come from the DATABASE_URL / UNIFLEET_DB_DSN env var.
"""

import os
from datetime import datetime, timezone
from typing import List, Dict, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from models import VOUCHER_COLUMNS


# VOUCHER_COLUMNS has 27 names; the schema has 29 (the 2 extras are
# the FK columns station_id and account_code). We pass through the
# FK columns when the caller provides them, else NULL.
_FK_COLUMNS = ("station_id", "account_code")
_VOUCHER_INSERT_COLUMNS = VOUCHER_COLUMNS + list(_FK_COLUMNS)

# Columns that the DB schema marks NOT NULL DEFAULT NOW() — when the
# caller doesn't provide them, set them in app code so the INSERT
# doesn't violate the NOT NULL constraint. (We could also omit them
# from the explicit INSERT and let DEFAULT NOW() fire, but doing it
# in code makes the auto-bump behavior on UPSERT cleaner.)
_AUTO_TIMESTAMP_COLUMNS = ("created_at", "updated_at")


def _nullable(v):
    """Convert empty string to None (CSV-world → Postgres convention)."""
    if v == "" or v is None:
        return None
    return v


def _now_or(v):
    """If v is missing or empty, return current UTC time; else return v."""
    if v is None or v == "":
        return datetime.now(timezone.utc)
    return v


class PostgresRepo:
    """Postgres-backed implementation of the Repo interface.

    All methods are safe to call concurrently. A connection pool
    (psycopg_pool.ConnectionPool) holds 1-8 connections; each method
    borrows one for the duration of a single transaction.
    """

    def __init__(self, dsn: Optional[str] = None,
                 min_size: int = 1, max_size: int = 8):
        if dsn is None:
            dsn = os.environ.get("DATABASE_URL") or os.environ.get("UNIFLEET_DB_DSN")
        if not dsn:
            raise ValueError(
                "PostgresRepo requires a DSN (pass dsn= or set DATABASE_URL / UNIFLEET_DB_DSN)"
            )
        self._dsn = dsn
        # open=False so we can attach event listeners before opening
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=30,
        )
        self._pool.open()
        self._pool.wait()  # ensure at least one connection is ready

    def close(self):
        """Close the pool. Idempotent; safe to call from teardown."""
        self._pool.close()

    def _row_to_dict(self, row) -> Dict:
        """psycopg.rows.dict_row already returns dicts; this is for
        backwards-compat / external callers that pass Row objects."""
        if row is None:
            return None
        if isinstance(row, dict):
            return row
        return {k: row[k] for k in row.keys()}

    # ============================================================
    # Reads
    # ============================================================

    def list_recent_vouchers(self, limit: int = 50) -> List[Dict]:
        """Return up to `limit` vouchers, newest first.

        Order: created_at DESC, transaction_date DESC NULLS LAST, voucher_id DESC.
        The voucher_id tiebreaker keeps results stable when created_at
        and transaction_date are both NULL.
        """
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    """
                    SELECT * FROM vouchers
                    ORDER BY
                        created_at DESC NULLS LAST,
                        transaction_date DESC NULLS LAST,
                        voucher_id DESC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                return cur.fetchall()

    def list_all_vouchers(self) -> List[Dict]:
        """Return every voucher, no order guarantee."""
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT * FROM vouchers")
                return cur.fetchall()

    def get_voucher(self, voucher_id: str) -> Optional[Dict]:
        """Return one voucher by ID, or None if not found."""
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT * FROM vouchers WHERE voucher_id = %s",
                    (voucher_id,),
                )
                return cur.fetchone()

    # ============================================================
    # Writes
    # ============================================================

    def set_status(self, voucher_id: str, new_status: str, redemption_timestamp: str):
        """Update status + redemption_timestamp; bump updated_at.

        `redemption_timestamp=""` (the CSV-world "not redeemed" signal)
        is stored as NULL. Callers that pass a real ISO 8601 string
        have it stored verbatim. `updated_at` is bumped to NOW().
        """
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE vouchers
                    SET status = %s,
                        redemption_timestamp = %s,
                        updated_at = NOW()
                    WHERE voucher_id = %s
                    """,
                    (new_status, _nullable(redemption_timestamp), voucher_id),
                )
                if cur.rowcount == 0:
                    raise KeyError(f"voucher not found: {voucher_id}")
            conn.commit()

    def append_vouchers(self, rows: List[Dict]):
        """Insert or upsert a batch of voucher rows.

        Empty list is a no-op. For each row, every key in
        _VOUCHER_INSERT_COLUMNS is looked up; missing keys become NULL.
        ON CONFLICT (voucher_id) DO UPDATE — the existing row is replaced
        (excluding the PK and the immutable `id`-style fields we don't have).
        """
        if not rows:
            return

        cols = _VOUCHER_INSERT_COLUMNS
        col_list = ", ".join(cols)
        placeholders = ", ".join(["%s"] * len(cols))
        # On conflict, update every non-PK column to the new value.
        # This is a "replace" UPSERT: caller is expected to provide the
        # full desired state of the row.
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "voucher_id")
        sql = (
            f"INSERT INTO vouchers ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (voucher_id) DO UPDATE SET {update_set}"
        )

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                for row in rows:
                    vals = [
                        _now_or(row.get(c)) if c in _AUTO_TIMESTAMP_COLUMNS
                        else _nullable(row.get(c))
                        for c in cols
                    ]
                    cur.execute(sql, vals)
            conn.commit()
