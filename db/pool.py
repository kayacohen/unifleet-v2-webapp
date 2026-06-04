"""
db/pool.py — shared Postgres connection pool singleton.

F2.3 of the UniFleet v2 → Railway + Postgres migration. The sidecar
stores (price_store, discount_store) and the PostgresRepo class
(F2.2) all need a connection pool pointing at the same DSN. This
module exposes a single `get_pool()` factory that lazily constructs
a `psycopg_pool.ConnectionPool` and reuses it for the process
lifetime.

The DSN is read from the DATABASE_URL or UNIFLEET_DB_DSN env var.
For tests, you can pass a DSN directly via the `dsn` parameter.
"""

import os
import threading
from typing import Optional

from psycopg_pool import ConnectionPool


_pool: Optional[ConnectionPool] = None
_pool_lock = threading.Lock()


def get_pool(dsn: Optional[str] = None,
             min_size: int = 1,
             max_size: int = 8,
             timeout: int = 30) -> ConnectionPool:
    """Return the shared ConnectionPool, constructing it on first use.

    If `dsn` is None, the DSN is read from DATABASE_URL or
    UNIFLEET_DB_DSN. Calling with a non-None `dsn` after the pool
    is already constructed is a no-op (the first DSN wins) — tests
    must call `reset_pool()` if they need a different DSN between
    tests.
    """
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is not None:
            return _pool

        if dsn is None:
            dsn = os.environ.get("DATABASE_URL") or os.environ.get("UNIFLEET_DB_DSN")
        if not dsn:
            raise ValueError(
                "db.get_pool() requires a DSN (pass dsn= or set "
                "DATABASE_URL / UNIFLEET_DB_DSN env var)"
            )

        pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=False,
            timeout=timeout,
        )
        pool.open()
        pool.wait()
        _pool = pool
        return _pool


def reset_pool() -> None:
    """Close and clear the shared pool. For tests / process shutdown."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
