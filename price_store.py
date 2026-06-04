"""
price_store.py — Postgres-backed station price store.

F2.3 of the UniFleet v2 → Railway + Postgres migration. Replaces the
JSON-on-disk implementation with a thin wrapper over the F2.1
schema's `stations` + `prices` + `price_history` tables. The public
function signatures are preserved so call sites in main.py and
generate_voucher.py do not change.

Public API (unchanged):
  init_if_missing()    no-op in PG (data is seeded by F2.1)
  load_all()           {"stations": [...]}  (back-compat shim)
  save_all(obj)        no-op in PG (use set_price / upsert_station)
  list_stations()      [Station dicts with id, brand, name, location,
                        price_php_per_liter, updated_at (epoch int)]
  get_station(id)      Single station dict, or None
  set_price(id, price) Updates price; appends to price_history
  upsert_station(st)   Inserts/updates station + price

The `_DEFAULT_STATIONS` constant is preserved (consumed by the F2.1
seed file and by tests/test_seeds.py for cross-validation).
"""

import os
import time
from typing import List, Dict, Any, Optional

import psycopg
from psycopg.rows import dict_row

from db.pool import get_pool


# ============================================================
# Default stations (preserved for F2.1 seed + test cross-check)
# ============================================================
_DEFAULT_STATIONS = [
    {
      "id": "cleanfuel_valenzuela",
      "brand": "Cleanfuel",
      "name": "Cleanfuel – Valenzuela",
      "location": "NLEX Southbound",
      "price_php_per_liter": 60.0,
      "updated_at": 1756654640
    },
    {
      "id": "unioil_mandaluyong",
      "brand": "Unioil",
      "name": "Unioil – Mandaluyong",
      "location": "EDSA",
      "price_php_per_liter": 59.1,
      "updated_at": 0
    },
    {
      "id": "seaoil_bicutan",
      "brand": "Seaoil",
      "name": "Seaoil – Bicutan",
      "location": "SLEX Northbound",
      "price_php_per_liter": 58.9,
      "updated_at": 0
    },
    {
      "id": "ecooil_qc",
      "brand": "EcoOil",
      "name": "EcoOil – QC",
      "location": "Commonwealth",
      "price_php_per_liter": 58.3,
      "updated_at": 0
    },
    {
      "id": "maximumfuel_val",
      "brand": "Maximum Fuel",
      "name": "Maximum Fuel – Valenzuela",
      "location": "Punturin",
      "price_php_per_liter": 57.95,
      "updated_at": 0
    },
    {
      "id": "phoenix_meyc",
      "brand": "Phoenix",
      "name": "Phoenix – Meycauayan",
      "location": "NLEX",
      "price_php_per_liter": 58.2,
      "updated_at": 0
    },
    {
      "id": "petro_gsanj",
      "brand": "Petro G",
      "name": "Petro G – San Jose",
      "location": "Bulacan",
      "price_php_per_liter": 58.0,
      "updated_at": 0
    },
    {
      "id": "gazz_binan",
      "brand": "Gazz",
      "name": "Gazz – Biñan",
      "location": "SLEX Southbound",
      "price_php_per_liter": 57.8,
      "updated_at": 0
    },
    {
      "id": "filoil_stamesa",
      "brand": "FilOil",
      "name": "FilOil – Sta. Mesa",
      "location": "Manila",
      "price_php_per_liter": 59.4,
      "updated_at": 0
    },
    {
      "id": "petron_port",
      "brand": "Petron",
      "name": "Petron – Port Area",
      "location": "Port of Manila",
      "price_php_per_liter": 59.9,
      "updated_at": 0
    }
]


# ============================================================
# Public API
# ============================================================

def init_if_missing() -> None:
    """No-op in PG. Stations are seeded by F2.1's db/seed_stations.sql.
    Kept as a no-op for back-compat with the JSON-era import-time call
    in main.py:105 (`price_store.init_if_missing()`).
    """
    return None


def load_all() -> Dict[str, Any]:
    """Return the whole data structure in legacy shape: {"stations": [...]}."""
    return {"stations": list_stations()}


def save_all(obj: Dict[str, Any]) -> None:
    """No-op in PG. The JSON-era callers wrote the whole blob in one
    shot, but in PG we use the targeted setters (set_price,
    upsert_station) so there's no equivalent operation. Kept as a
    no-op for back-compat with any code that still calls it.
    """
    return None


def list_stations() -> List[Dict[str, Any]]:
    """Return all stations joined with their current price.

    Each row: {id, brand, name, location, price_php_per_liter, updated_at}
    - `updated_at` is the price's `updated_at` converted to Unix epoch
      seconds (int), matching the legacy JSON shape so existing
      call sites like `int(s.get("updated_at", 0) or 0)` keep working.
    - Stations without a row in `prices` (NULL price) still appear,
      with price_php_per_liter=None and updated_at=0.
    """
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT
                    s.id,
                    s.brand,
                    s.display_name AS name,
                    s.location,
                    p.price_php_per_liter,
                    COALESCE(EXTRACT(EPOCH FROM p.updated_at)::BIGINT, 0) AS updated_at
                FROM stations s
                LEFT JOIN prices p ON p.station_id = s.id
                ORDER BY s.brand, s.display_name
            """)
            rows = cur.fetchall()
    # Coerce Decimal -> float and None -> 0 for the price (callers expect
    # numeric values; the legacy JSON had real numbers).
    out = []
    for r in rows:
        price = r.get("price_php_per_liter")
        if price is not None:
            price = float(price)
        out.append({
            "id": r["id"],
            "brand": r["brand"],
            "name": r["name"],
            "location": r["location"],
            "price_php_per_liter": price,
            "updated_at": int(r.get("updated_at") or 0),
        })
    return out


def get_station(station_id: str) -> Optional[Dict[str, Any]]:
    """Return a single station dict by id, or None if not found."""
    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("""
                SELECT
                    s.id,
                    s.brand,
                    s.display_name AS name,
                    s.location,
                    p.price_php_per_liter,
                    COALESCE(EXTRACT(EPOCH FROM p.updated_at)::BIGINT, 0) AS updated_at
                FROM stations s
                LEFT JOIN prices p ON p.station_id = s.id
                WHERE s.id = %s
            """, (station_id,))
            r = cur.fetchone()
    if r is None:
        return None
    price = r.get("price_php_per_liter")
    if price is not None:
        price = float(price)
    return {
        "id": r["id"],
        "brand": r["brand"],
        "name": r["name"],
        "location": r["location"],
        "price_php_per_liter": price,
        "updated_at": int(r.get("updated_at") or 0),
    }


def set_price(station_id: str, new_price: float) -> Dict[str, Any]:
    """Update a station's price; append a row to price_history.

    Returns the updated station dict (same shape as get_station).
    Raises ValueError if the new price is out of range, KeyError if
    the station does not exist.
    """
    if new_price is None or new_price <= 0 or new_price > 200:
        raise ValueError("Unreasonable price. Must be 0 < price ≤ 200.")

    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            # Read the old price for the history row (NULL if no prior price).
            cur.execute(
                "SELECT price_php_per_liter FROM prices WHERE station_id = %s",
                (station_id,),
            )
            old_row = cur.fetchone()
            if old_row is None and not _station_exists(cur, station_id):
                raise KeyError(f"Station '{station_id}' not found")
            old_price = old_row["price_php_per_liter"] if old_row else None

            # UPSERT the price.
            cur.execute("""
                INSERT INTO prices (station_id, price_php_per_liter, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (station_id) DO UPDATE
                SET price_php_per_liter = EXCLUDED.price_php_per_liter,
                    updated_at = NOW()
            """, (station_id, round(float(new_price), 2)))

            # Append to price_history. actor_ip and user_agent are
            # NULL by default; a future revision of the caller can
            # pass them in if/when the admin UI starts sending them.
            cur.execute("""
                INSERT INTO price_history
                    (station_id, old_price, new_price, timestamp_iso, timestamp_unix)
                VALUES
                    (%s, %s, %s, NOW(), EXTRACT(EPOCH FROM NOW())::BIGINT)
            """, (station_id, old_price, round(float(new_price), 2)))
        conn.commit()

    # Return the updated station dict (fresh from the DB)
    updated = get_station(station_id)
    assert updated is not None
    return updated


def upsert_station(st: Dict[str, Any]) -> Dict[str, Any]:
    """Add or replace a station + its price in one shot.

    Required keys: id, brand, name, location, price_php_per_liter.
    """
    required = {"id", "brand", "name", "location", "price_php_per_liter"}
    if not required.issubset(st.keys()):
        missing = required - set(st.keys())
        raise ValueError(f"Missing keys: {missing}")

    pool = get_pool()
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO stations (id, brand, display_name, location, is_active)
                VALUES (%s, %s, %s, %s, TRUE)
                ON CONFLICT (id) DO UPDATE
                SET brand = EXCLUDED.brand,
                    display_name = EXCLUDED.display_name,
                    location = EXCLUDED.location,
                    updated_at = NOW()
            """, (st["id"], st["brand"], st["name"], st.get("location")))

            cur.execute("""
                INSERT INTO prices (station_id, price_php_per_liter, updated_at)
                VALUES (%s, %s, NOW())
                ON CONFLICT (station_id) DO UPDATE
                SET price_php_per_liter = EXCLUDED.price_php_per_liter,
                    updated_at = NOW()
            """, (st["id"], round(float(st["price_php_per_liter"]), 2)))
        conn.commit()

    return get_station(st["id"])


# ============================================================
# Internal helpers
# ============================================================

def _station_exists(cur, station_id: str) -> bool:
    """Return True if a station row exists with this id (cheap existence check)."""
    cur.execute("SELECT 1 FROM stations WHERE id = %s", (station_id,))
    return cur.fetchone() is not None
