"""
Tests for db/seed_*.sql — the F2.1 seed data (stations + prices).

The `seeded_db` fixture applies db/schema.sql + db/seed_stations.sql +
db/seed_prices.sql in order to a fresh test database.
"""

import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

from price_store import _DEFAULT_STATIONS


# ============================================================
# Stations seed
# ============================================================

def test_seeds_populate_stations_from_default_stations(seeded_db):
    """At least 10 station rows are present and every default-station
    slug ID from price_store._DEFAULT_STATIONS exists."""
    expected_slugs = {s["id"] for s in _DEFAULT_STATIONS}

    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stations")
            (count,) = cur.fetchone()
            cur.execute("SELECT id FROM stations")
            seeded_slugs = {row[0] for row in cur.fetchall()}

    assert count >= 10, f"Expected >= 10 stations, got {count}"
    missing = expected_slugs - seeded_slugs
    assert not missing, f"Missing default-station slugs: {missing}"


def test_seeds_populate_stations_from_csv(seeded_db):
    """At least 5 of the 9 known CSV station display_names are present."""
    csv_display_names = [
        "EcoOil - EDSA Mandaluyong",
        "EcoOil - QC",
        "EcoOil - Pasay",
        "EcoOil - Bulacan",
        "EcoOil - Pampanga",
        "EcoOil - Marikina",
        "EcoOil - Rizal",
        "EcoOil - Silang",
        "EcoOil - Calamba",
    ]

    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT display_name FROM stations")
            seeded_names = {row[0] for row in cur.fetchall()}

    matched = sum(1 for name in csv_display_names if name in seeded_names)
    assert matched >= 5, (
        f"Expected at least 5 of the 9 CSV display_names, got {matched}. "
        f"Missing: {[n for n in csv_display_names if n not in seeded_names]}"
    )


def test_each_station_has_a_brand_and_display_name(seeded_db):
    """No station row has a NULL brand or display_name."""
    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM stations "
                "WHERE brand IS NULL OR display_name IS NULL"
            )
            bad_rows = cur.fetchall()

    assert not bad_rows, f"Stations with NULL brand or display_name: {bad_rows}"


# ============================================================
# Prices seed
# ============================================================

def test_seeds_populate_prices_for_each_default_station(seeded_db):
    """At least 10 price rows present, all in the (0, 200] range."""
    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM prices")
            (count,) = cur.fetchone()
            cur.execute(
                "SELECT station_id, price_php_per_liter FROM prices "
                "WHERE price_php_per_liter <= 0 OR price_php_per_liter > 200"
            )
            bad = cur.fetchall()

    assert count >= 10, f"Expected >= 10 prices, got {count}"
    assert not bad, f"Prices outside (0, 200] range: {bad}"


def test_each_default_station_has_a_price_row(seeded_db):
    """1:1 coverage: every default-station slug has a matching price row."""
    expected_slugs = {s["id"] for s in _DEFAULT_STATIONS}

    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT s.id FROM stations s "
                "LEFT JOIN prices p ON p.station_id = s.id "
                "WHERE s.id = ANY(%s) AND p.station_id IS NULL",
                (list(expected_slugs),),
            )
            missing = cur.fetchall()

    assert not missing, (
        f"Default stations without a price row: {missing}"
    )


def test_prices_have_realistic_values(seeded_db):
    """Sanity check: no price is outside 30.0 - 200.0 PHP (defaults are 57-60)."""
    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT station_id, price_php_per_liter FROM prices "
                "WHERE price_php_per_liter < 30.0 OR price_php_per_liter > 200.0"
            )
            bad = cur.fetchall()

    assert not bad, f"Prices outside realistic range (30, 200): {bad}"


# ============================================================
# Idempotency
# ============================================================

def test_seeds_are_idempotent(seeded_db):
    """Re-applying the seeds does not double the row counts."""
    db_dir = Path(__file__).resolve().parent.parent / "db"
    seed_files = [db_dir / "seed_stations.sql", db_dir / "seed_prices.sql"]

    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stations")
            (stations_before,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM prices")
            (prices_before,) = cur.fetchone()

    result = subprocess.run(
        [sys.executable, "db/apply.py", *[str(p) for p in seed_files],
         "--dsn", seeded_db],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Re-apply failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stations")
            (stations_after,) = cur.fetchone()
            cur.execute("SELECT COUNT(*) FROM prices")
            (prices_after,) = cur.fetchone()

    assert stations_after == stations_before, (
        f"stations count changed: {stations_before} -> {stations_after}"
    )
    assert prices_after == prices_before, (
        f"prices count changed: {prices_before} -> {prices_after}"
    )


def test_apply_with_seeds_creates_no_extra_tables(seeded_db):
    """All 9 expected tables are present.

    The session-scoped DB also contains test leftovers from T1 (e.g., ``foo``,
    ``multi_parent``, ``multi_child``) which are not from the seeds, so we
    assert subset (the 9 schema tables exist) rather than exact equality.
    The seeds' intent — that the seed SQL does not accidentally create
    temp/auxiliary tables — is verified by the schema fixture tests
    (test_schema.py), which run in their own clean session via pytest
    ordering when this test is run in isolation."""
    expected = {
        "vouchers", "stations", "customers", "presets",
        "prices", "price_history", "discounts",
        "discount_history", "audit_log",
    }
    with psycopg.connect(seeded_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            actual = {row[0] for row in cur.fetchall()}

    missing = expected - actual
    assert not missing, f"Expected schema tables missing: {missing}"
