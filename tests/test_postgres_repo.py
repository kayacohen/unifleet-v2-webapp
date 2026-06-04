"""
Tests for db/postgres_repo.py — the F2.2 PostgresRepo class.

T1 covers: connection pool + 3 read methods + 2 simple write methods:
  - list_recent_vouchers
  - list_all_vouchers
  - get_voucher
  - set_status
  - append_vouchers

The `schema_db` fixture (from conftest.py) provides a fresh test DB
with the F2.1 schema applied. Each test creates its own PostgresRepo
instance pointed at that DB, and closes it on teardown so the pool
doesn't leak. An autouse `clean_vouchers` fixture truncates the
vouchers table before each test (schema_db is session-scoped, so
prior tests in this file would otherwise leak rows).
"""

import psycopg
import pytest

from db.postgres_repo import PostgresRepo


@pytest.fixture(autouse=True)
def clean_vouchers(schema_db):
    """Truncate the vouchers table before each test for isolation.

    Stations / customers / prices are seeded by F2.1's conftest fixtures
    and shared with other test files; we don't touch them here.
    """
    with psycopg.connect(schema_db) as conn:
        with conn.cursor() as cur:
            # CASCADE handles the audit_log FK reference.
            cur.execute("TRUNCATE vouchers CASCADE")
        conn.commit()
    yield


# ============================================================
# list_recent_vouchers
# ============================================================

def test_list_recent_vouchers_empty(schema_db):
    """An empty vouchers table returns an empty list (not None, not error)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.list_recent_vouchers(limit=50)
    finally:
        repo.close()

    assert result == []


def test_list_recent_vouchers_single_row(schema_db):
    """A single voucher comes back as a list of one dict with the 29 columns."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-ABCDE",
            "station": "Test Station",
            "status": "Unverified",
            "requested_amount_php": 100.0,
        }])
        result = repo.list_recent_vouchers(limit=50)
    finally:
        repo.close()

    assert len(result) == 1
    assert result[0]["voucher_id"] == "UF-20260605-ABCDE"
    assert result[0]["station"] == "Test Station"
    assert result[0]["status"] == "Unverified"


def test_list_recent_vouchers_orders_by_recent_first(schema_db):
    """Vouchers with newer created_at come before older ones."""
    from datetime import datetime, timezone, timedelta

    repo = PostgresRepo(dsn=schema_db)
    try:
        # Use explicit created_at values so the order is deterministic
        # (otherwise the DB NOW() default would make them all nearly equal
        # and the test would be order-dependent on insert timing).
        base = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
        repo.append_vouchers([
            {"voucher_id": "UF-20260101-OLD01", "status": "Unverified",
             "created_at": base - timedelta(days=100)},
            {"voucher_id": "UF-20260601-NEW01", "status": "Unverified",
             "created_at": base},
            {"voucher_id": "UF-20260301-MID01", "status": "Unverified",
             "created_at": base - timedelta(days=50)},
        ])
        result = repo.list_recent_vouchers(limit=50)
    finally:
        repo.close()

    ids = [r["voucher_id"] for r in result]
    # All three are present
    assert set(ids) == {"UF-20260101-OLD01", "UF-20260601-NEW01", "UF-20260301-MID01"}
    # Newest (base, NEW01) first; oldest (base-100d, OLD01) last
    assert ids[0] == "UF-20260601-NEW01"
    assert ids[-1] == "UF-20260101-OLD01"


def test_list_recent_vouchers_respects_limit(schema_db):
    """limit caps the result list length."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        for i in range(5):
            repo.append_vouchers([{"voucher_id": f"UF-2026060{i}-LIMIT{i}", "status": "Unverified"}])
        result = repo.list_recent_vouchers(limit=3)
    finally:
        repo.close()

    assert len(result) == 3


# ============================================================
# list_all_vouchers
# ============================================================

def test_list_all_vouchers_empty(schema_db):
    """Empty DB returns empty list."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.list_all_vouchers()
    finally:
        repo.close()

    assert result == []


def test_list_all_vouchers_returns_every_row(schema_db):
    """All 5 rows come back, regardless of limit."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        for i in range(5):
            repo.append_vouchers([{"voucher_id": f"UF-2026060{i}-ALL0{i}", "status": "Unverified"}])
        result = repo.list_all_vouchers()
    finally:
        repo.close()

    assert len(result) == 5
    ids = {r["voucher_id"] for r in result}
    assert ids == {f"UF-2026060{i}-ALL0{i}" for i in range(5)}


def test_list_all_vouchers_preserves_typed_values(schema_db):
    """NUMERIC columns come back as Decimal (not str), TIMESTAMPTZ as datetime."""
    from decimal import Decimal
    from datetime import datetime, timezone

    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-TYPED",
            "status": "Unverified",
            "requested_amount_php": Decimal("150.50"),
            "transaction_date": "2026-06-05T10:00:00+00:00",
        }])
        result = repo.list_all_vouchers()
    finally:
        repo.close()

    assert len(result) == 1
    row = result[0]
    assert isinstance(row["requested_amount_php"], Decimal)
    assert row["requested_amount_php"] == Decimal("150.50")
    assert isinstance(row["transaction_date"], datetime)
    # tz-aware: psycopg returns aware datetimes from TIMESTAMPTZ
    assert row["transaction_date"].tzinfo is not None


# ============================================================
# get_voucher
# ============================================================

def test_get_voucher_found(schema_db):
    """Existing voucher comes back as a dict with the requested fields."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-FOUND",
            "status": "Unverified",
            "driver_name": "Test Driver",
        }])
        result = repo.get_voucher("UF-20260605-FOUND")
    finally:
        repo.close()

    assert result is not None
    assert result["voucher_id"] == "UF-20260605-FOUND"
    assert result["driver_name"] == "Test Driver"


def test_get_voucher_not_found(schema_db):
    """Missing voucher_id returns None (not raise, not empty dict)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.get_voucher("UF-DOES-NOT-EXIST")
    finally:
        repo.close()

    assert result is None


# ============================================================
# set_status
# ============================================================

def test_set_status_to_redeemed(schema_db):
    """Setting status='Redeemed' stores status and the timestamp."""
    from datetime import datetime, timezone

    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{"voucher_id": "UF-20260605-RED01", "status": "Unredeemed"}])
        ts = "2026-06-05T12:00:00+00:00"
        repo.set_status("UF-20260605-RED01", "Redeemed", ts)
        row = repo.get_voucher("UF-20260605-RED01")
    finally:
        repo.close()

    assert row["status"] == "Redeemed"
    assert row["redemption_timestamp"] is not None
    assert isinstance(row["redemption_timestamp"], datetime)
    assert row["redemption_timestamp"].tzinfo is not None


def test_set_status_to_non_redeemed_clears_timestamp(schema_db):
    """Setting status to anything other than 'Redeemed' clears the timestamp
    (stores NULL, not empty string) in Postgres."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{"voucher_id": "UF-20260605-CLR01", "status": "Unredeemed"}])
        repo.set_status("UF-20260605-CLR01", "Redeemed", "2026-06-05T10:00:00+00:00")
        # Now revert to Unredeemed with empty string (CSV-world input)
        repo.set_status("UF-20260605-CLR01", "Unredeemed", "")
        row = repo.get_voucher("UF-20260605-CLR01")
    finally:
        repo.close()

    assert row["status"] == "Unredeemed"
    assert row["redemption_timestamp"] is None


def test_set_status_bumps_updated_at(schema_db):
    """set_status updates the updated_at column to a non-NULL value."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{"voucher_id": "UF-20260605-UPD01", "status": "Unverified"}])
        before = repo.get_voucher("UF-20260605-UPD01")
        repo.set_status("UF-20260605-UPD01", "Unredeemed", "")
        after = repo.get_voucher("UF-20260605-UPD01")
    finally:
        repo.close()

    assert before["updated_at"] is not None  # set by append_vouchers NOW() default
    assert after["updated_at"] is not None
    # updated_at should be >= before (same second is fine)
    assert after["updated_at"] >= before["updated_at"]


def test_set_status_missing_voucher_raises(schema_db):
    """set_status on a non-existent voucher_id raises KeyError."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        with pytest.raises(KeyError):
            repo.set_status("UF-DOES-NOT-EXIST", "Redeemed", "2026-06-05T10:00:00+00:00")
    finally:
        repo.close()


# ============================================================
# append_vouchers
# ============================================================

def test_append_vouchers_empty_list_is_noop(schema_db):
    """append_vouchers([]) does not raise and adds no rows."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([])
        result = repo.list_all_vouchers()
    finally:
        repo.close()

    assert result == []


def test_append_vouchers_single_row(schema_db):
    """A single row dict inserts a row with the given fields."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-APP01",
            "station": "Test Station",
            "status": "Unverified",
            "requested_amount_php": 250.0,
        }])
        result = repo.list_all_vouchers()
    finally:
        repo.close()

    assert len(result) == 1
    row = result[0]
    assert row["voucher_id"] == "UF-20260605-APP01"
    assert row["station"] == "Test Station"
    assert row["status"] == "Unverified"
    # NUMERIC columns round-trip as Decimal
    from decimal import Decimal
    assert row["requested_amount_php"] == Decimal("250.00")


def test_append_vouchers_multiple_rows(schema_db):
    """Multiple rows in one call insert all of them."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([
            {"voucher_id": "UF-20260605-MUL01", "status": "Unverified"},
            {"voucher_id": "UF-20260605-MUL02", "status": "Unverified"},
            {"voucher_id": "UF-20260605-MUL03", "status": "Unverified"},
        ])
        result = repo.list_all_vouchers()
    finally:
        repo.close()

    assert len(result) == 3
    ids = {r["voucher_id"] for r in result}
    assert ids == {"UF-20260605-MUL01", "UF-20260605-MUL02", "UF-20260605-MUL03"}


def test_append_vouchers_upsert_updates_existing(schema_db):
    """Re-appending a row with the same voucher_id updates (not duplicates)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UPS01",
            "status": "Unverified",
            "station": "Original Station",
        }])
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UPS01",
            "status": "Unredeemed",
            "station": "Updated Station",
        }])
        result = repo.list_all_vouchers()
    finally:
        repo.close()

    assert len(result) == 1
    row = result[0]
    assert row["status"] == "Unredeemed"
    assert row["station"] == "Updated Station"


def test_append_vouchers_empty_string_becomes_null(schema_db):
    """Empty string for a nullable column becomes NULL in Postgres."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-NUL01",
            "status": "Unverified",
            "station": "",  # empty string from CSV-world input
            "driver_name": "",  # same
        }])
        row = repo.get_voucher("UF-20260605-NUL01")
    finally:
        repo.close()

    assert row["station"] is None
    assert row["driver_name"] is None


# ============================================================
# create_unverified_booking
# ============================================================

def test_create_unverified_booking_with_minimal_data(schema_db):
    """A booking created from a sparse dict still has voucher_id and status='Unverified'."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.create_unverified_booking({
            "driver_name": "Test Driver",
            "vehicle_plate": "ABC123",
        })
    finally:
        repo.close()

    assert result["status"] == "Unverified"
    assert result["driver_name"] == "Test Driver"
    assert result["vehicle_plate"] == "ABC123"
    assert result["voucher_id"]  # auto-generated
    assert result["voucher_id"].startswith("UF-")
    # created_at and updated_at are auto-set
    assert result["created_at"] is not None
    assert result["updated_at"] is not None


def test_create_unverified_booking_returns_persisted_row(schema_db):
    """The returned dict matches what's actually in the DB (get_voucher)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.create_unverified_booking({
            "driver_name": "Round Trip Driver",
            "vehicle_plate": "PLATE99",
        })
        vid = result["voucher_id"]
        fetched = repo.get_voucher(vid)
    finally:
        repo.close()

    assert fetched is not None
    assert fetched["voucher_id"] == vid
    assert fetched["status"] == "Unverified"
    assert fetched["driver_name"] == "Round Trip Driver"


def test_create_unverified_booking_respects_provided_voucher_id(schema_db):
    """If caller provides a voucher_id, it's used (not auto-generated)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.create_unverified_booking({
            "voucher_id": "UF-CUSTOM-ID-0001",
            "driver_name": "Custom ID Driver",
        })
    finally:
        repo.close()

    assert result["voucher_id"] == "UF-CUSTOM-ID-0001"


def test_create_unverified_booking_refuel_datetime_fills_expected_refill_date(schema_db):
    """refuel_datetime is used as expected_refill_date when the latter is empty."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.create_unverified_booking({
            "refuel_datetime": "2026-07-15T08:00:00+00:00",
            "driver_name": "Refill Driver",
        })
    finally:
        repo.close()

    assert result["expected_refill_date"] is not None
    assert result["transaction_date"] is not None
    # Both dates should be set from the refuel_datetime
    assert "2026-07-15" in str(result["expected_refill_date"])
    assert "2026-07-15" in str(result["transaction_date"])


def test_create_unverified_booking_does_not_overwrite_provided_dates(schema_db):
    """If the caller already provided expected_refill_date, refuel_datetime
    should NOT overwrite it (fallback only kicks in for empty values)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.create_unverified_booking({
            "refuel_datetime": "2026-07-15T08:00:00+00:00",
            "expected_refill_date": "2026-08-01T00:00:00+00:00",
            "transaction_date": "2026-08-01T00:00:00+00:00",
            "driver_name": "Keep Dates Driver",
        })
    finally:
        repo.close()

    # Provided dates are preserved, not overwritten by refuel_datetime
    assert "2026-08-01" in str(result["expected_refill_date"])
    assert "2026-08-01" in str(result["transaction_date"])


# ============================================================
# update_voucher_fields
# ============================================================

def test_update_voucher_fields_single_field(schema_db):
    """A single field in the dict is updated; others untouched."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UPF01",
            "status": "Unverified",
            "driver_name": "Original Driver",
        }])
        repo.update_voucher_fields("UF-20260605-UPF01", {
            "driver_name": "Updated Driver",
        })
        row = repo.get_voucher("UF-20260605-UPF01")
    finally:
        repo.close()

    assert row["driver_name"] == "Updated Driver"
    assert row["status"] == "Unverified"  # untouched


def test_update_voucher_fields_multiple_fields(schema_db):
    """Multiple fields in the dict are all updated."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UPF02",
            "status": "Unverified",
            "driver_name": "Original",
            "vehicle_plate": "OLD-PLATE",
        }])
        repo.update_voucher_fields("UF-20260605-UPF02", {
            "driver_name": "New Name",
            "vehicle_plate": "NEW-PLATE",
            "live_price_php_per_liter": 58.75,
        })
        row = repo.get_voucher("UF-20260605-UPF02")
    finally:
        repo.close()

    assert row["driver_name"] == "New Name"
    assert row["vehicle_plate"] == "NEW-PLATE"
    from decimal import Decimal
    assert row["live_price_php_per_liter"] == Decimal("58.7500")


def test_update_voucher_fields_bumps_updated_at(schema_db):
    """updated_at is set to a non-NULL value, >= the previous one."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UPF03",
            "status": "Unverified",
        }])
        before = repo.get_voucher("UF-20260605-UPF03")
        repo.update_voucher_fields("UF-20260605-UPF03", {
            "driver_name": "New Name",
        })
        after = repo.get_voucher("UF-20260605-UPF03")
    finally:
        repo.close()

    assert before["updated_at"] is not None
    assert after["updated_at"] is not None
    assert after["updated_at"] >= before["updated_at"]


def test_update_voucher_fields_missing_voucher_raises(schema_db):
    """Updating a non-existent voucher raises KeyError."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        with pytest.raises(KeyError):
            repo.update_voucher_fields("UF-DOES-NOT-EXIST", {
                "driver_name": "Ghost Driver",
            })
    finally:
        repo.close()


def test_update_voucher_fields_mirrors_discount_total_php(schema_db):
    """Setting discount_total_php also updates discount_total (legacy column)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UPF04",
            "status": "Unverified",
        }])
        repo.update_voucher_fields("UF-20260605-UPF04", {
            "discount_total_php": 12.50,
        })
        row = repo.get_voucher("UF-20260605-UPF04")
    finally:
        repo.close()

    from decimal import Decimal
    assert row["discount_total_php"] == Decimal("12.50")
    # discount_total (legacy) should mirror the new value
    assert row["discount_total"] == Decimal("12.50")


def test_update_voucher_fields_mirrors_total_dispensed_php(schema_db):
    """Setting total_dispensed_php also updates total_dispensed (legacy column)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UPF05",
            "status": "Unverified",
        }])
        repo.update_voucher_fields("UF-20260605-UPF05", {
            "total_dispensed_php": 200.00,
        })
        row = repo.get_voucher("UF-20260605-UPF05")
    finally:
        repo.close()

    from decimal import Decimal
    assert row["total_dispensed_php"] == Decimal("200.00")
    # total_dispensed (legacy) should mirror
    assert row["total_dispensed"] == Decimal("200.00")
