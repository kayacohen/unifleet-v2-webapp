# models.py

VOUCHER_COLUMNS = [
    # identifiers
    "voucher_id",

    # booking data
    "station",
    "requested_amount_php",
    "liters_requested",
    "transaction_date",
    "expected_refill_date",

    # legacy live-price fields (kept for backward compatibility)
    "live_price_php_per_liter",
    "discount_per_liter",
    "discount_total",
    "total_dispensed",
    "liters_dispensed",

    # driver/vehicle
    "driver_name",
    "vehicle_plate",
    "truck_make",
    "truck_model",
    "number_of_wheels",

    # status + redemption
    "status",
    "redemption_timestamp",

    # audit timestamps (UTC; displayed as Manila via template filter)
    "created_at",
    "updated_at",

    # ---- NEW: booking-time snapshot fields (source-of-truth at Verify) ----
    "price_snapshot_php_per_liter",
    "price_snapshot_updated_at",
    "discount_snapshot_php_per_liter",
    "discount_snapshot_captured_at",

    # ---- NEW: computed-at-Verify fields (used in PNG math) ----
    "discount_total_php",
    "total_dispensed_php",
    "computed_at",
]


SQLITE_PATH = "data/unifleet.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vouchers (
  voucher_id TEXT PRIMARY KEY,
  station TEXT,
  requested_amount_php REAL,
  liters_requested REAL,
  transaction_date TEXT,
  expected_refill_date TEXT,
  live_price_php_per_liter REAL,
  discount_per_liter REAL,
  discount_total REAL,
  total_dispensed REAL,
  liters_dispensed REAL,
  driver_name TEXT,
  vehicle_plate TEXT,
  truck_make TEXT,
  truck_model TEXT,
  number_of_wheels TEXT,
  status TEXT,
  redemption_timestamp TEXT
);
"""
