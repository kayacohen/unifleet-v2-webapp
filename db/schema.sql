-- db/schema.sql — UniFleet v2 Postgres schema
-- Phase 2 of the UniFleet v2 → Railway + Postgres migration.
-- Apply with: python db/apply.py db/schema.sql --dsn <DSN>
--
-- 9 tables: vouchers (wide), stations, customers, presets, prices,
-- price_history, discounts, discount_history, audit_log.
--
-- Design notes:
--   * Hybrid: one wide `vouchers` table (29 cols) + normalized
--     `stations` / `customers` FK tables.
--   * Slug IDs for stations (`ecooil_qc`); `legacy_id` is a separate
--     UNIQUE column for back-compat with the existing CSV data.
--   * TIMESTAMPTZ everywhere. NUMERIC for all monetary/quantity values
--     (never FLOAT).
--   * BIGSERIAL for append-only tables (audit_log, price_history,
--     discount_history); VARCHAR / NUMERIC for human-facing IDs.
--   * Forward-only: CREATE TABLE IF NOT EXISTS. No DROP, no
--     schema-mutation logic. Idempotency is a property of the SQL.
--   * No CHECK constraints (F3.x territory).
--   * No triggers (F2.5 territory).

-- ============================================================
-- Stations: 10 rows seeded by T3 (cleanfuel_valenzuela, ecooil_qc,
-- unioil_mandaluyong, etc.). `legacy_id` carries the integer IDs
-- from data/stations.csv (1-10) for back-compat with the existing
-- voucher data.
-- ============================================================
CREATE TABLE IF NOT EXISTS stations (
    id          VARCHAR(64)  PRIMARY KEY,
    legacy_id   VARCHAR(64)  UNIQUE,
    brand       VARCHAR(100) NOT NULL,
    name        VARCHAR(200) NOT NULL,
    location    VARCHAR(200),
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Customers: account_code is the primary key. The 4-char codes in
-- data/customers.csv (HARR, JETI, ...) fit comfortably in VARCHAR(16).
-- ============================================================
CREATE TABLE IF NOT EXISTS customers (
    account_code  VARCHAR(16)  PRIMARY KEY,
    contact_name  VARCHAR(200),
    contact_number VARCHAR(50),
    email         VARCHAR(200),
    company_name  VARCHAR(200),
    fleet_size    INTEGER,
    areas         VARCHAR(200),
    refuel_locations VARCHAR(200),
    hq_locations  VARCHAR(200),
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Vouchers: the wide central table. 29 columns = 27 from
-- models.VOUCHER_COLUMNS + station_id (FK) + account_code (FK).
-- `station` (denormalized human name) is kept alongside `station_id`
-- (the FK slug) for back-compat with the existing CSV exports.
-- ============================================================
CREATE TABLE IF NOT EXISTS vouchers (
    voucher_id                       VARCHAR(32)  PRIMARY KEY,
    station_id                       VARCHAR(64)  REFERENCES stations(id),
    station                          VARCHAR(200),
    account_code                     VARCHAR(16)  REFERENCES customers(account_code),

    requested_amount_php             NUMERIC(12,2),
    liters_requested                 NUMERIC(12,4),
    transaction_date                 TIMESTAMPTZ,
    expected_refill_date             TIMESTAMPTZ,

    live_price_php_per_liter         NUMERIC(10,4),
    discount_per_liter               NUMERIC(8,4),
    discount_total                   NUMERIC(12,2),

    total_dispensed                  NUMERIC(12,4),
    liters_dispensed                 NUMERIC(12,4),

    driver_name                      VARCHAR(200),
    vehicle_plate                    VARCHAR(20),
    truck_make                       VARCHAR(50),
    truck_model                      VARCHAR(50),
    number_of_wheels                 SMALLINT,

    status                           VARCHAR(50)  NOT NULL DEFAULT 'Unverified',
    redemption_timestamp             TIMESTAMPTZ,

    created_at                       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at                       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    price_snapshot_php_per_liter     NUMERIC(10,4),
    price_snapshot_updated_at        TIMESTAMPTZ,
    discount_snapshot_php_per_liter  NUMERIC(8,4),
    discount_snapshot_captured_at    TIMESTAMPTZ,

    discount_total_php               NUMERIC(12,2),
    total_dispensed_php              NUMERIC(12,2),
    computed_at                      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vouchers_status            ON vouchers(status);
CREATE INDEX IF NOT EXISTS idx_vouchers_transaction_date  ON vouchers(transaction_date);
CREATE INDEX IF NOT EXISTS idx_vouchers_created_at        ON vouchers(created_at);

-- ============================================================
-- Presets: per-customer driver/vehicle defaults. UNIQUE on
-- (account_code, driver_name) prevents duplicate presets for the
-- same driver.
-- ============================================================
CREATE TABLE IF NOT EXISTS presets (
    id                BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_code      VARCHAR(16)  NOT NULL REFERENCES customers(account_code),
    driver_name       VARCHAR(200) NOT NULL,
    vehicle_plate     VARCHAR(20),
    truck_make        VARCHAR(50),
    truck_model       VARCHAR(50),
    number_of_wheels  SMALLINT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_presets_account_driver UNIQUE (account_code, driver_name)
);

-- ============================================================
-- Prices: current price per station. 1:1 with stations.
-- ============================================================
CREATE TABLE IF NOT EXISTS prices (
    station_id              VARCHAR(64)  PRIMARY KEY REFERENCES stations(id),
    price_php_per_liter     NUMERIC(10,4) NOT NULL,
    updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Price history: append-only audit of every price change.
-- Mirrors data/price_history.csv (PRICE_HISTORY_FIELDS in main.py).
-- ============================================================
CREATE TABLE IF NOT EXISTS price_history (
    id              BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id      VARCHAR(64)  NOT NULL REFERENCES stations(id),
    old_price       NUMERIC(10,4),
    new_price       NUMERIC(10,4) NOT NULL,
    timestamp_iso   TIMESTAMPTZ  NOT NULL,
    timestamp_unix  BIGINT       NOT NULL,
    actor_ip        VARCHAR(50),
    user_agent      TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_history_station_id
    ON price_history(station_id);

-- ============================================================
-- Discounts: current discount_per_liter per station. 1:1 with stations.
-- ============================================================
CREATE TABLE IF NOT EXISTS discounts (
    station_id            VARCHAR(64)  PRIMARY KEY REFERENCES stations(id),
    discount_per_liter    NUMERIC(8,4) NOT NULL,
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- ============================================================
-- Discount history: append-only audit of every discount change.
-- Mirrors data/discount_history.csv columns.
-- ============================================================
CREATE TABLE IF NOT EXISTS discount_history (
    id                       BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id               VARCHAR(64)  NOT NULL REFERENCES stations(id),
    old_discount_per_liter   NUMERIC(8,4),
    new_discount_per_liter   NUMERIC(8,4) NOT NULL,
    timestamp_iso            TIMESTAMPTZ  NOT NULL,
    actor                    VARCHAR(100),
    reason                   TEXT
);

CREATE INDEX IF NOT EXISTS idx_discount_history_station_id
    ON discount_history(station_id);

-- ============================================================
-- Audit log: append-only record of every operator action.
-- Mirrors AUDIT_FIELDS in main.py:128-132.
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    timestamp     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    action        VARCHAR(50)  NOT NULL,
    voucher_id    VARCHAR(32)  REFERENCES vouchers(voucher_id),
    from_status   VARCHAR(50),
    to_status     VARCHAR(50),
    route         VARCHAR(200),
    actor_ip      VARCHAR(50),
    user_agent    TEXT,
    note          TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
    ON audit_log(timestamp);
