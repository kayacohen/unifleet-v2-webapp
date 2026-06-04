"""
End-to-end integration test for the F2.2 PostgresRepo.

Walks the full voucher lifecycle (book → approve → compute totals →
redeem) through a single repo instance, asserting the state of the
row at every step. This catches cross-method interaction bugs that
the per-method tests in test_postgres_repo.py miss.
"""

from datetime import datetime, timezone

import psycopg
import pytest

from db.postgres_repo import PostgresRepo


def test_full_lifecycle_book_approve_redeem(schema_db):
    """Book -> Approve -> Compute totals -> Redeem, asserting state at each step."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        # ---------- 1. Book (Unverified) ----------
        booked = repo.create_unverified_booking({
            "driver_name": "Lifecycle Driver",
            "vehicle_plate": "LIF-001",
            "truck_make": "Isuzu",
            "truck_model": "NQR",
            "number_of_wheels": 4,
            "requested_amount_php": 1500.0,
            "refuel_datetime": "2026-06-05T08:00:00+00:00",
        })
        vid = booked["voucher_id"]
        assert booked["status"] == "Unverified"
        assert booked["redemption_timestamp"] is None

        # ---------- 2. Approve (Unredeemed) ----------
        repo.set_status(vid, "Unredeemed", "")
        row = repo.get_voucher(vid)
        assert row["status"] == "Unredeemed"
        assert row["redemption_timestamp"] is None

        # ---------- 3. Compute totals (operator action) ----------
        repo.update_voucher_fields(vid, {
            "live_price_php_per_liter": 60.00,
            "discount_per_liter": 1.50,
            "liters_requested": 25.0000,
            "discount_total_php": 37.50,
            "total_dispensed_php": 1500.00,
            "liters_dispensed": 25.0000,
            "computed_at": datetime.now(timezone.utc),
        })
        row = repo.get_voucher(vid)
        assert row["status"] == "Unredeemed"  # status didn't change
        from decimal import Decimal
        assert row["live_price_php_per_liter"] == Decimal("60.0000")
        assert row["discount_total"] == Decimal("37.50")        # mirror
        assert row["total_dispensed"] == Decimal("1500.00")     # mirror

        # ---------- 4. Redeem ----------
        redeem_ts = "2026-06-05T14:30:00+00:00"
        repo.set_status(vid, "Redeemed", redeem_ts)
        row = repo.get_voucher(vid)
        assert row["status"] == "Redeemed"
        assert row["redemption_timestamp"] is not None
        assert "2026-06-05" in str(row["redemption_timestamp"])
        # All the computed totals are still there
        assert row["total_dispensed"] == Decimal("1500.00")
        assert row["discount_total"] == Decimal("37.50")
    finally:
        repo.close()


def test_list_recent_vouchers_sees_newly_booked_voucher(schema_db):
    """After a booking + approval, the voucher shows up in list_recent_vouchers."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        # Capture baseline
        before = repo.list_recent_vouchers(limit=50)

        # Book a new voucher
        booked = repo.create_unverified_booking({
            "driver_name": "Recent Driver",
            "vehicle_plate": "REC-002",
        })
        vid = booked["voucher_id"]

        # It should appear in the recent list
        after = repo.list_recent_vouchers(limit=50)
        after_ids = {r["voucher_id"] for r in after}
        assert vid in after_ids
        # The recent list grew by at least 1
        assert len(after) >= len(before) + 1
    finally:
        repo.close()


def test_redeem_then_unredeem_clears_timestamp(schema_db):
    """Redeem sets the timestamp; reverting to Unredeemed clears it (sets NULL)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        booked = repo.create_unverified_booking({
            "driver_name": "Revert Driver",
            "vehicle_plate": "REV-003",
        })
        vid = booked["voucher_id"]

        # Promote to Unredeemed, then Redeemed
        repo.set_status(vid, "Unredeemed", "")
        repo.set_status(vid, "Redeemed", "2026-06-05T15:00:00+00:00")
        row = repo.get_voucher(vid)
        assert row["status"] == "Redeemed"
        assert row["redemption_timestamp"] is not None

        # Revert to Unredeemed (the "operator undoes redemption" flow)
        repo.set_status(vid, "Unredeemed", "")
        row = repo.get_voucher(vid)
        assert row["status"] == "Unredeemed"
        assert row["redemption_timestamp"] is None
    finally:
        repo.close()


def test_get_voucher_returns_none_for_never_existed_voucher_id(schema_db):
    """A voucher_id that was never inserted returns None, not a row with NULL fields."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        result = repo.get_voucher("UF-NEVER-EXISTED-XX")
    finally:
        repo.close()

    assert result is None


def test_update_unknown_column_is_silently_ignored(schema_db):
    """An unknown column in update_voucher_fields is dropped (Postgres
    rejects ALTER, but unknown column names in the field dict are just
    filtered out — caller doesn't get a TypeError, the valid fields
    are still applied)."""
    repo = PostgresRepo(dsn=schema_db)
    try:
        repo.append_vouchers([{
            "voucher_id": "UF-20260605-UNK01",
            "status": "Unverified",
            "driver_name": "Original Name",
        }])
        repo.update_voucher_fields("UF-20260605-UNK01", {
            "driver_name": "Updated Name",
            "totally_made_up_column": "ignored",
        })
        row = repo.get_voucher("UF-20260605-UNK01")
    finally:
        repo.close()

    assert row["driver_name"] == "Updated Name"
