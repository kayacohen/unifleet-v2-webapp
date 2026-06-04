# Plan: F2.1 — Postgres Schema

> **Date:** 2026-06-04
> **Project source:** `specs/plans/PROJECT-migrate-to-railway.md` (Phase 2, feature F2.1)
> **Estimated tasks:** 3-4
> **Planning session:** detailed

## Summary

Define the Postgres schema that replaces the CSV-based persistence layer. Eight tables, all written to a single `db/schema.sql` file and applied idempotently by `db/apply.py`. The `vouchers` table is one wide row (28+ columns) for CSV parity and trivial data migration; `stations`, `customers`, `presets`, `prices`, `price_history`, `discounts`, `discount_history`, and `audit_log` are normalized out as separate tables. No data is migrated and no code is changed in F2.1 — this feature is **schema only**, applied to a throwaway or dev database. F2.2 implements the `PostgresRepo` against this schema; F2.5 migrates the live data.

## Requirements

### Functional Requirements
1. A single `db/schema.sql` file defines all eight tables in dependency order (parent tables before child tables, regardless of declaration order is fine since Postgres resolves FKs at the end of a transaction).
2. `db/apply.py` connects to a Postgres database (via `$DATABASE_URL` or a passed DSN), drops the schema if it already exists in a controlled way, and applies the DDL idempotently (using `CREATE TABLE IF NOT EXISTS` for forward compatibility). It exits 0 on success, non-zero on failure.
3. The `vouchers` table contains every column in `models.VOUCHER_COLUMNS` (28 columns) as defined at `models.py:3-37`, plus 2 audit columns (`created_at`, `updated_at` — already in the list) and 1 lifecycle column (`_legacy_csv_rowid` — nullable, for the data migration to map old row order to the new primary key).
4. The `stations` table is the single source of truth for station identity. It unifies three current representations: the `station_id` (int) from `data/stations.csv`, the slug ID (e.g. `ecooil_qc`) from `data/station_prices.json`, and the free-text station name used in vouchers. The voucher table has a `station_id` FK to this table.
5. The `customers` table is keyed by `account_code` (matches `data/customers.csv` PK semantics).
6. The `audit_log` table replaces `data/ops_audit_log.csv` with the same 9 columns and identical append-only semantics.
7. The `prices` and `discounts` tables replace the current JSON sidecar stores (`data/station_prices.json`, `data/discount_store.json`) with one row per (station, value, as_of). The `_DEFAULT_STATIONS` list in `price_store.py:9-90` is the seed.
8. The `price_history` and `discount_history` tables replace the current `data/price_history.csv` and `data/discount_history.csv` with one row per change event, append-only.
9. The `presets` table replaces `data/presets/{account_code}_presets.csv` — one row per (account_code, driver_name) combination.

### Non-Functional Requirements
1. **Idempotency:** `db/apply.py` can be run repeatedly against the same database without error. Re-running must not destroy existing data.
2. **No new runtime dependencies:** the schema is plain SQL; the apply script uses `psycopg` (already in `pyproject.toml`) and stdlib.
3. **Schema lives in the repo, not in someone's head:** `db/schema.sql` is the single source of truth. The apply script reads it, no string concatenation in the apply script itself.
4. **All `TIMESTAMP` columns are `TIMESTAMPTZ`** (timezone-aware), even though the existing CSVs are timezone-naive. The apply script or F2.5 migration will normalize on the way in.
5. **All `MONEY` columns are `NUMERIC(12, 2)`** — exact decimal arithmetic, no float drift. Existing CSVs use plain text or float; conversion is the migration's job.
6. **All IDs that are user-facing (voucher_id, account_code) are `VARCHAR`** with explicit length caps. Internal surrogate keys (audit_log.id, price_history.id) are `BIGSERIAL`.
7. **Schema is forward-compatible with F2.2's `PostgresRepo`:** every column referenced in `persistence.py:CSVRepo` and the public call sites in `main.py` exists in the schema.

## Behaviors

### Schema design

**Why rules matter:**

- The `vouchers` table's 28-column wide shape mirrors `VOUCHER_COLUMNS` exactly. The plan doc's "schema parity is the #1 silent-data-loss vector" warning is real: if the F2.5 migration maps a CSV column to a differently-named or differently-typed Postgres column, the row is silently wrong. One wide row means one-to-one column mapping.
- `stations` and `customers` are split out as FK tables because they are shared entities (a customer can have many vouchers; a station can be referenced by many vouchers). Normalizing these is cheap (one extra JOIN per voucher read, which F2.2's connection pool amortizes) and prevents the data-quality issues that come from free-text station/customer strings in voucher rows.
- `audit_log`, `price_history`, `discount_history` are append-only tables. The `id BIGSERIAL PRIMARY KEY` provides insertion order; the existing CSV's "append" semantics are preserved as `INSERT` (no `UPDATE`/`DELETE`).
- `prices` and `discounts` are snapshot-of-current-state tables, **not** history tables. The `_history` tables hold the audit trail; these hold the latest value. This split matches the current `station_prices.json` (current state) + `price_history.csv` (history) pattern.

**What's optional vs required:**

- Required: every column in `VOUCHER_COLUMNS`; every key in the JSON stores; every column in the audit/history CSVs.
- Optional: composite indexes beyond the obvious FK/PK indexes. F2.1 includes the minimum useful indexes (voucher_id PK, station_id FK, account_code FK, audit_log timestamp, prices station_id). F3.2's tests can add more if a query needs them.
- Optional: `CHECK` constraints on price ranges, status enum values, etc. These belong to F2.2 (the repo can enforce them in code; the schema can grow them in a follow-up).
- Optional: `updated_at` triggers to auto-bump on UPDATE. Same logic — F2.2 territory.

**Common mistakes:**

- Adding `NOT NULL` to columns that exist as empty strings in the CSV (e.g., `redemption_timestamp` is empty until the voucher is redeemed). Use `DEFAULT ''` or `DEFAULT NULL` and let the data migration decide.
- Using `SERIAL` or `BIGSERIAL` for `voucher_id` — this would break the existing UF-YYYYMMDD-XXXXX format used in QR codes and the supplier API. The voucher_id stays as `VARCHAR(32) PRIMARY KEY` with the UF-… format.
- Forgetting that `data/templates/HARR_presets.sample.csv` has a UTF-8 BOM (`\ufeff` in the column header). The apply script or migration should handle that, but the schema itself doesn't care.
- Treating `data/requested_vouchers.csv` as a table to migrate. It's an input queue, not a master ledger — it stays as a CSV (or is dropped entirely; see Open Questions).

### Apply script

**Why rules matter:**

- A schema that lives only in a developer's head is a schema that drifts. `db/apply.py` reads `db/schema.sql` verbatim and executes it, so the file is the single source of truth.
- Idempotency matters because the script will be re-run on the local dev DB (in Docker), on the Railway dev DB, and on the Railway production DB. A partial failure should not require a manual cleanup.

**What's optional vs required:**

- Required: read the SQL file, connect with `psycopg.connect(DATABASE_URL, connect_timeout=5)`, execute the SQL, exit 0/non-0.
- Optional: a `--reset` flag that drops everything first. F2.1 omits this; the user can `DROP SCHEMA public CASCADE` manually if they want a full reset.

**Common mistakes:**

- Concatenating the SQL into a Python string. The apply script must read the file as a string and pass it to `psycopg`'s `execute` or `cursor.execute` directly. No template strings, no f-strings.
- Forgetting that `db/schema.sql` may contain semicolons inside `CREATE FUNCTION` bodies or other constructs. The script executes the whole file as one statement; that's fine for DDL but not for any future PL/pgSQL.
- Not setting a connection timeout. The default is unlimited, which is bad for a script that should fail fast.

### Testing the schema

**Why rules matter:**

- A schema can be syntactically valid but semantically wrong (e.g., a typo in a column name that matches no actual data). The test suite is the contract that the F2.2 `PostgresRepo` will rely on.
- Tests also serve as living documentation of the schema for future contributors.

**What's optional vs required:**

- Required: tests that connect to a real Postgres (the local Docker one), apply the schema, and assert table existence + column shapes.
- Optional: round-trip tests that insert sample rows and read them back. F2.2 owns this — it needs a real `PostgresRepo` to write the round-trip tests.

**Common mistakes:**

- Mocking the database. Schema tests must hit a real Postgres; otherwise they test nothing. The local Docker Compose setup provides this.
- Hardcoding test data that conflicts with `_DEFAULT_STATIONS` or the live `data/stations.csv` / `data/customers.csv` files. Use small, distinct test fixtures.

## Detailed Specifications

### Table: `vouchers` (wide, CSV-parity)

**Purpose:** the master voucher ledger. One row per voucher. Replaces `data/master_vouchers.csv`.

**Columns (mirroring `models.VOUCHER_COLUMNS`):**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `voucher_id` | `VARCHAR(32) PRIMARY KEY` | NOT NULL | — | e.g. `UF-20260604-AB12C` |
| `station_id` | `VARCHAR(64) REFERENCES stations(id)` | NULL | NULL | FK to the unified `stations` table |
| `station_name_legacy` | `VARCHAR(128)` | NULL | NULL | the free-text name from the original CSV, kept for the supplier API and QR codes until F2.6 retires them |
| `requested_amount_php` | `NUMERIC(12, 2)` | NULL | NULL | |
| `liters_requested` | `NUMERIC(10, 3)` | NULL | NULL | |
| `transaction_date` | `TIMESTAMPTZ` | NULL | NULL | |
| `expected_refill_date` | `TIMESTAMPTZ` | NULL | NULL | |
| `live_price_php_per_liter` | `NUMERIC(10, 4)` | NULL | NULL | |
| `discount_per_liter` | `NUMERIC(8, 4)` | NULL | NULL | |
| `discount_total` | `NUMERIC(12, 2)` | NULL | NULL | |
| `total_dispensed` | `NUMERIC(12, 2)` | NULL | NULL | |
| `liters_dispensed` | `NUMERIC(10, 3)` | NULL | NULL | |
| `driver_name` | `VARCHAR(128)` | NULL | NULL | |
| `vehicle_plate` | `VARCHAR(32)` | NULL | NULL | |
| `truck_make` | `VARCHAR(64)` | NULL | NULL | |
| `truck_model` | `VARCHAR(64)` | NULL | NULL | |
| `number_of_wheels` | `VARCHAR(8)` | NULL | NULL | text (matches CSV) |
| `status` | `VARCHAR(16)` | NOT NULL | `'Unverified'` | enum-like, not enforced at schema level |
| `redemption_timestamp` | `TIMESTAMPTZ` | NULL | NULL | |
| `created_at` | `TIMESTAMPTZ` | NULL | NULL | |
| `updated_at` | `TIMESTAMPTZ` | NULL | NULL | |
| `price_snapshot_php_per_liter` | `NUMERIC(10, 4)` | NULL | NULL | |
| `price_snapshot_updated_at` | `TIMESTAMPTZ` | NULL | NULL | |
| `discount_snapshot_php_per_liter` | `NUMERIC(8, 4)` | NULL | NULL | |
| `discount_snapshot_captured_at` | `TIMESTAMPTZ` | NULL | NULL | |
| `discount_total_php` | `NUMERIC(12, 2)` | NULL | NULL | legacy mirror of `discount_total` |
| `total_dispensed_php` | `NUMERIC(12, 2)` | NULL | NULL | legacy mirror of `total_dispensed` |
| `computed_at` | `TIMESTAMPTZ` | NULL | NULL | |
| `account_code` | `VARCHAR(16) REFERENCES customers(account_code)` | NULL | NULL | FK to `customers`; the link from voucher to customer |
| `enforce_phases_at_create` | `BOOLEAN` | NOT NULL | `false` | whether the booking was created with `ENFORCE_PHASES=1`; the migration sets this from the audit log if it can |

**Indexes:**

- `voucher_id` PK (auto)
- `station_id` (FK index, auto)
- `account_code` (FK index, auto)
- `status` (filtered often: admin pages, list_recent_vouchers)
- `transaction_date` (sorted by)
- `created_at` (sorted by for `list_recent_vouchers`)

### Table: `stations` (unified station identity)

**Purpose:** the single source of truth for station identity. Replaces the three current representations: `data/stations.csv` (int ID + name), `data/station_prices.json` (slug ID), and the free-text station name in `vouchers.station_name_legacy`.

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | `VARCHAR(64) PRIMARY KEY` | NOT NULL | — | the slug, e.g. `ecooil_qc` |
| `legacy_id` | `INTEGER` | NULL | NULL | the int from `data/stations.csv`; UNIQUE within the table to enforce 1:1 |
| `brand` | `VARCHAR(64)` | NOT NULL | — | e.g. `EcoOil` |
| `display_name` | `VARCHAR(128)` | NOT NULL | — | e.g. `EcoOil - QC` |
| `location` | `VARCHAR(128)` | NULL | NULL | e.g. `Commonwealth` |
| `is_active` | `BOOLEAN` | NOT NULL | `true` | soft-delete for stations that get retired |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | |

**Seed data:** the 10 stations from `price_store.py:_DEFAULT_STATIONS` plus the 10 from `data/stations.csv` (after de-duping by name). The seed lives in `db/seed_stations.sql` and is applied by the same `db/apply.py` script as part of the schema apply.

**Common mistakes:**

- Confusing the slug ID with the legacy int ID. The slug is the canonical PK; the int is just a denormalization for backward compat.
- Putting the price in this table. Prices change over time; the `prices` table is the history-of-current-value, `price_history` is the audit trail. Don't conflate.

### Table: `customers`

**Purpose:** the customer master. Replaces `data/customers.csv`.

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `account_code` | `VARCHAR(16) PRIMARY KEY` | NOT NULL | — | e.g. `HARR` |
| `contact_name` | `VARCHAR(128)` | NULL | NULL | |
| `contact_number` | `VARCHAR(32)` | NULL | NULL | |
| `email` | `VARCHAR(128)` | NULL | NULL | |
| `company_name` | `VARCHAR(128)` | NULL | NULL | |
| `fleet_size` | `INTEGER` | NULL | NULL | |
| `areas` | `TEXT` | NULL | NULL | free-text (matches CSV) |
| `refuel_locations` | `TEXT` | NULL | NULL | |
| `hq_locations` | `TEXT` | NULL | NULL | |
| `is_active` | `BOOLEAN` | NOT NULL | `true` | soft-delete |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | |

**Note:** the existing `data/customers.csv` has duplicate `account_code` values (e.g., `JAML` appears twice with different contact info — see rows 5 and 9 in the file). The F2.5 migration will need to deduplicate; F2.1's schema doesn't enforce uniqueness beyond the PK.

### Table: `presets`

**Purpose:** per-customer driver/vehicle presets. Replaces `data/presets/{account_code}_presets.csv`.

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | `BIGSERIAL PRIMARY KEY` | NOT NULL | auto | surrogate |
| `account_code` | `VARCHAR(16) REFERENCES customers(account_code)` | NOT NULL | — | |
| `driver_name` | `VARCHAR(128)` | NOT NULL | — | |
| `vehicle_plate` | `VARCHAR(32)` | NULL | NULL | |
| `truck_make` | `VARCHAR(64)` | NULL | NULL | |
| `truck_model` | `VARCHAR(64)` | NULL | NULL | |
| `number_of_wheels` | `VARCHAR(8)` | NULL | NULL | |
| `fuel_type` | `VARCHAR(32)` | NULL | NULL | |

**Unique constraint:** `(account_code, driver_name)` — one preset per (account, driver). The current CSV is keyed by row order, not by any natural key, so the migration will need a deduplication rule.

### Table: `prices` (current state)

**Purpose:** the current price per station. Replaces the `stations` array in `data/station_prices.json`.

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `station_id` | `VARCHAR(64) REFERENCES stations(id) PRIMARY KEY` | NOT NULL | — | one row per station |
| `price_php_per_liter` | `NUMERIC(10, 4)` | NOT NULL | — | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | the last update timestamp |

**Seed:** the 10 prices from `price_store.py:_DEFAULT_STATIONS`. The seed lives in `db/seed_prices.sql`.

### Table: `price_history` (audit trail)

**Purpose:** the audit trail of every price change. Replaces `data/price_history.csv` (which doesn't exist yet but the code at `main.py:163` is ready to write it).

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | `BIGSERIAL PRIMARY KEY` | NOT NULL | auto | |
| `station_id` | `VARCHAR(64) REFERENCES stations(id)` | NOT NULL | — | |
| `old_price` | `NUMERIC(10, 4)` | NULL | NULL | NULL on first-ever update |
| `new_price` | `NUMERIC(10, 4)` | NOT NULL | — | |
| `timestamp_iso` | `TIMESTAMPTZ` | NOT NULL | `now()` | |
| `timestamp_unix` | `BIGINT` | NOT NULL | — | epoch seconds; matches the current CSV's `timestamp_unix` column |
| `actor_ip` | `VARCHAR(64)` | NULL | NULL | |
| `user_agent` | `VARCHAR(256)` | NULL | NULL | |

### Table: `discounts` (current state)

**Purpose:** the current discount per station. Replaces `data/discount_store.json`.

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `station_id` | `VARCHAR(64) REFERENCES stations(id) PRIMARY KEY` | NOT NULL | — | one row per station |
| `discount_per_liter` | `NUMERIC(8, 4)` | NOT NULL | — | |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | |

**Note:** the current `data/discount_store.json` is empty (no discounts set). The seed is empty.

### Table: `discount_history` (audit trail)

**Purpose:** the audit trail of every discount change. Replaces `data/discount_history.csv`.

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | `BIGSERIAL PRIMARY KEY` | NOT NULL | auto | |
| `station_id` | `VARCHAR(64) REFERENCES stations(id)` | NOT NULL | — | |
| `old_discount_per_liter` | `NUMERIC(8, 4)` | NULL | NULL | |
| `new_discount_per_liter` | `NUMERIC(8, 4)` | NULL | NULL | NULL means "removed" |
| `timestamp_iso` | `TIMESTAMPTZ` | NOT NULL | `now()` | matches the current CSV's `timestamp_iso` (Asia/Manila local) — the apply normalizes |
| `actor` | `VARCHAR(64)` | NOT NULL | `'system'` | |
| `reason` | `VARCHAR(256)` | NULL | NULL | |

### Table: `audit_log`

**Purpose:** the operational audit log. Replaces `data/ops_audit_log.csv` (defined at `main.py:128-132`).

**Columns:**

| Column | Type | Nullable | Default | Notes |
|--------|------|----------|---------|-------|
| `id` | `BIGSERIAL PRIMARY KEY` | NOT NULL | auto | |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL | `now()` | matches the CSV's first column |
| `action` | `VARCHAR(32)` | NOT NULL | — | e.g. `redeem`, `redeem_denied`, `ops_set_status` |
| `voucher_id` | `VARCHAR(32) REFERENCES vouchers(voucher_id)` | NULL | NULL | may be NULL for non-voucher events |
| `from_status` | `VARCHAR(16)` | NULL | NULL | |
| `to_status` | `VARCHAR(16)` | NULL | NULL | |
| `route` | `VARCHAR(128)` | NULL | NULL | |
| `actor_ip` | `VARCHAR(64)` | NULL | NULL | |
| `user_agent` | `VARCHAR(256)` | NULL | NULL | |
| `note` | `TEXT` | NULL | NULL | |

**Indexes:**

- `timestamp` (filtered by date range in admin views)
- `voucher_id` (FK index, auto; useful for "all events for voucher X")

## Key Constraints

| Constraint | Why It Matters |
|------------|----------------|
| `db/schema.sql` is the single source of truth for the schema. | Drift between "what we designed" and "what's in the DB" is the most common way a schema project goes wrong. The apply script reads this file verbatim. |
| `voucher_id` stays as `VARCHAR(32)` in the `UF-YYYYMMDD-XXXXX` format. | QR codes, the supplier API (`/supplier-api/<voucher_id>`), and existing customer audit trails all reference the voucher_id. Changing the format would break all three. |
| The `stations` table is seeded with both the 10 price_store defaults and the 10 stations.csv rows. | F2.5 needs both representations to be present to map between them. Seeding only one would force the migration to guess. |
| All `TIMESTAMP` columns are `TIMESTAMPTZ`, not `TIMESTAMP`. | The current CSVs mix naive and Manila-local ISO strings. F2.5 normalizes them on the way in; the schema must accept both. `TIMESTAMPTZ` is the right default. |
| All money columns are `NUMERIC`, not `FLOAT` or `REAL`. | PHP currency math with floats is a known footgun. The CSV's float columns get converted to NUMERIC in F2.5. |
| `audit_log`, `price_history`, `discount_history` are append-only at the schema level. | Adding a `BEFORE UPDATE OR DELETE` trigger that raises an exception is the F2.5 (or follow-up) job. F2.1 documents the intent; enforcement is F2.5. |
| `db/apply.py` reads `db/schema.sql` as a single string and executes it. | No string interpolation, no template substitution. Schema drift is impossible if the script is dumb. |
| Schema must be re-runnable against the same database without error. | The script will be run on local dev, Railway dev, and Railway prod at least once each, often more. |

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|----------|----------|-----------|
| The schema has a syntax error and `db/apply.py` fails partway. | The script aborts on the first error. The user inspects, fixes the SQL, re-runs. Postgres DDL is transactional for most operations, so a partial apply is rare. | A schema with a typo is worse than no schema. Fail fast. |
| `db/apply.py` is run against a database that has the old (different) schema. | The script does NOT drop the old schema. It uses `CREATE TABLE IF NOT EXISTS`. If a column is missing, the new table is created but with the old column shape, which F2.2's repo will hit at runtime. | The apply script is a forward-only tool. Migrations of existing data are F2.5's job, not F2.1's. |
| Two developers run `db/apply.py` at the same time against the same DB. | `CREATE TABLE IF NOT EXISTS` is atomic; the second call is a no-op. No data loss. | Race conditions on schema apply are rare in this project (single-team); making the script cluster-safe is over-engineering. |
| The `_DEFAULT_STATIONS` in `price_store.py` have a slug that conflicts with the legacy int ID. | The seed scripts (`db/seed_stations.sql`, `db/seed_prices.sql`) use a merge step: insert if missing, update if exists. Conflict is resolved by the slug winning. | The slug is the canonical PK; the int is a denormalization. |
| A test runs `db/apply.py` against the live Railway DB by mistake. | The script reads `$DATABASE_URL`; in tests it must be overridden to a test DB. `tests/conftest.py` sets `DATABASE_URL` to a per-test schema name. | Tests must never touch production. |
| A voucher has a `station_name_legacy` that doesn't match any current `stations.display_name`. | The `station_id` FK is NULL; the legacy name is preserved. The F2.5 migration flags these for review. | The data is what it is; the schema should not lose it. |
| The customer `account_code` is missing on a voucher (some old rows might not have it). | `vouchers.account_code` is NULLABLE. F2.5 reports the count of vouchers with NULL `account_code`. | Don't lose old data over a missing FK. |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|------------------------|----------------|
| 1 | Single wide `vouchers` table (28 columns) | Normalized into `vouchers` + `voucher_pricing` + `voucher_assets` | CSV parity = trivial migration; one-to-one column mapping eliminates the #1 silent-data-loss risk |
| 2 | `stations` and `customers` normalized as separate FK tables | Free-text strings in `vouchers` | Shared entities deserve their own table; prevents data-quality issues from name variations |
| 3 | `stations.id` is a slug (e.g. `ecooil_qc`), not the int from `stations.csv` | Int ID, UUID | Slugs are human-readable, match `price_store.py`, sort/searchable; the int stays as `legacy_id` for back-compat |
| 4 | Raw SQL + `db/apply.py` (psycopg) | Alembic, SQLAlchemy declarative | Smallest dep footprint, single source of truth in one file, no learning curve. Alembic is a clean follow-up if schema changes start piling up. |
| 5 | `TIMESTAMPTZ` everywhere | `TIMESTAMP` (naive), `TEXT` (ISO strings) | Naive timestamps are the current bug; `TIMESTAMPTZ` is the right Postgres default. The migration normalizes on the way in. |
| 6 | `NUMERIC(12, 2)` for money, `NUMERIC(10, 4)` for prices, `NUMERIC(8, 4)` for discounts | `FLOAT`, `REAL`, `DOUBLE PRECISION` | PHP currency math with floats is a known footgun. Exact decimal is the right call. |
| 7 | `BIGSERIAL` surrogate PK for `audit_log`, `price_history`, `discount_history`, `presets` | UUID, natural key | Append-only tables don't benefit from UUIDs; BIGSERIAL gives insertion order for free |
| 8 | `voucher_id` stays as `VARCHAR(32)` in the `UF-YYYYMMDD-XXXXX` format | `SERIAL` / `BIGSERIAL` | QR codes and supplier API already reference this format. Changing it would break the supplier integration. |
| 9 | Append-only tables (`audit_log`, `price_history`, `discount_history`) have no `updated_at` column | Add `updated_at` for consistency | They're append-only; there's no update to timestamp. The `timestamp` / `timestamp_iso` IS the append timestamp. |
| 10 | Schema is forward-only: `db/apply.py` does not drop or alter existing tables | Drop-and-recreate, full migration tool | The schema is small enough to redeploy from scratch. F2.5 handles the data migration. |
| 11 | `is_active BOOLEAN` for soft-delete on `stations` and `customers` | Hard delete | Soft-delete is the right default for shared entities that other tables FK to |
| 12 | `station_name_legacy` is preserved on the voucher row even after the FK link | Drop it after F2.5 | The supplier API and QR codes reference the legacy name; removing it would break those. Cleanup is F2.6 (file asset pipeline). |

## Scope Boundaries

### In Scope
- `db/schema.sql` — all 8 tables, indexes, constraints
- `db/apply.py` — idempotent apply script using `psycopg` and `$DATABASE_URL`
- `db/seed_stations.sql` and `db/seed_prices.sql` — seed data from `_DEFAULT_STATIONS` and `data/stations.csv`
- `tests/test_schema.py` — connect to a real Postgres, apply, assert table existence and column shapes
- `tests/conftest.py` updates — fixture that points the test DB at the local Docker Postgres (and isolates each test with its own schema)

### Out of Scope
- The `PostgresRepo` implementation (F2.2)
- Migrating live data from the CSVs/JSON to the new tables (F2.5)
- Replacing `price_store.py` and `discount_store.py` to read from the new tables (F2.3)
- Replacing the audit-log write path in `main.py:append_audit` to use the new `audit_log` table (F2.4)
- File asset pipeline on Volume (F2.6)
- `data/requested_vouchers.csv` as a table — it's an input queue, not a master ledger; the project plan does not call for migrating it. Stays as a CSV in the meantime. (See Open Questions.)
- Real auth, CSRF, structured logging, phase ordering, helper dedup — all Phase 3
- Alembic or any other migration tool — the simple `db/apply.py` is enough for the current scope; adding Alembic is a F3+ follow-up

## Dependencies

### Depends On (must exist before this work starts)
- A working Postgres database. The local Docker Compose setup (`db` service in `docker-compose.yml`) provides this.
- `psycopg[binary]` in `pyproject.toml` — already added in F1.1.
- `data/stations.csv` and `data/station_prices.json` (for the seed data) — already in the repo.

### Depended On By (other work waiting for this)
- **F2.2 (`PostgresRepo`)** — needs the schema to write SQL against.
- **F2.3 (sidecar stores)** — needs `prices`, `discounts`, `price_history`, `discount_history` tables.
- **F2.4 (audit log)** — needs `audit_log` table.
- **F2.5 (data migration script)** — needs the schema as the target shape.
- **F2.6 (file asset pipeline)** — references the volume mount path; unrelated to schema but related to the persistence layer.
- **F3.2 (test suite)** — needs the schema for `PostgresRepo` round-trip tests.
- **F4.6 (architecture / schema reference doc)** — needs the schema as the source of truth.

## Architecture Notes

- The schema is intentionally **wide-and-flat** for the `vouchers` table. The alternative (normalized) was rejected because the CSV is wide-and-flat, and the F2.5 data migration is the most risk-prone part of the phase. A 1:1 column mapping removes that risk.
- The `stations` and `customers` tables are the only normalized-out tables. This is the smallest possible normalization that fixes the data-quality issues (station identity, customer linking) without paying the JOIN cost for every voucher read.
- The `audit_log`, `price_history`, `discount_history` tables are append-only by convention. Enforcing append-only at the schema level (via triggers) is left as a follow-up. F2.1 documents the intent.
- The `db/apply.py` script is intentionally dumb: read file, connect, execute. Any intelligence (migrations, version checks, schema diff) belongs in a follow-up tool. This is a sharp tool for a specific job.
- The seed data lives in separate `db/seed_*.sql` files, not inline in `db/schema.sql`. The apply script runs schema first, then seeds. This separation lets us re-seed without re-applying the schema (e.g., adding a new station without recreating all tables).

## Open Questions

- **`data/requested_vouchers.csv` — table or stays as CSV?**
  - **Impact if unresolved:** If it's a table, F2.1 should add it; if not, F2.5 ignores it and F1.3's env-var management decides its future.
  - **Suggested default:** Stays as a CSV. The project plan does not call for migrating it; it's an input queue that's overwritten on each upload. A future ticket can decide.
- **Are duplicate `account_code` values in `data/customers.csv` a real bug or sample data?**
  - **Impact if unresolved:** F2.5's customer migration needs a deduplication rule. The plan says to "report the count" but not how to handle.
  - **Suggested default:** F2.5 keeps the most recent row (by `created_at` or by row order in the CSV) and reports the discarded count. The schema doesn't enforce a uniqueness rule; the migration handles it.
- **Does the F2.1 apply script need to handle "schema already exists with different columns"?**
  - **Impact if unresolved:** If a developer has manually edited the local DB and the schema no longer matches `db/schema.sql`, the apply script silently succeeds (because `CREATE TABLE IF NOT EXISTS` is a no-op) and F2.2's repo fails at runtime.
  - **Suggested default:** F2.1 does NOT detect this. F2.2's tests will catch it. A `--strict` flag in `db/apply.py` is a clean follow-up.
- **Should `audit_log` have a CHECK constraint on `action`?**
  - **Impact if unresolved:** Without one, a typo in the code (`'reedem'` instead of `'redeem'`) is silently accepted.
  - **Suggested default:** No CHECK constraint in F2.1. Adding one requires enumerating all valid `action` values, which the code currently doesn't centralize. F3.x is the right time to centralize and constrain.

---
_This plan is the input for the generate-tasks skill._
_Review this document, then run: "Generate task from plan: specs/plans/PLAN-postgres-schema.md"_

---

# Tasks

## Task T1: `db/apply.py` — generic SQL applier

> **Status:** done
> **Effort:** s
> **Priority:** critical
> **Depends on:** None

### Description

A small Python script that connects to a Postgres database (via `$DATABASE_URL` or a `--dsn` flag), reads one or more SQL files in order, and executes them. Idempotent: re-running against the same database must not error. Used by T2 to apply `db/schema.sql` and T3 to apply the seed SQL files. This task is **purely tooling** — no app-specific schema, no app-specific seed data, no app code changes.

### Test Plan

#### Test File(s)
- `tests/test_apply.py`
- `tests/conftest.py` (add a `postgres_db` session-scoped fixture that creates a fresh `unifleet_test_<uuid>` database, yields the DSN, drops the database on teardown)

#### Test Scenarios

##### Connect / apply / disconnect

- **`test_apply_runs_a_trivial_sql_file_and_returns_zero`** — GIVEN a fixture SQL file containing `CREATE TABLE foo (id INT);` WHEN `apply.py path/to/fixture.sql` is invoked THEN exit code is 0 AND a `foo` table exists in the database.
- **`test_apply_accepts_dsn_from_env_var`** — GIVEN `DATABASE_URL` is set to the test DSN WHEN `apply.py path/to/fixture.sql` is invoked (no `--dsn`) THEN the script connects to the URL from the env var.
- **`test_apply_accepts_explicit_dsn_flag`** — GIVEN a `--dsn` flag is passed WHEN invoked THEN the script connects to that DSN, not the env var.
- **`test_apply_returns_nonzero_on_connection_failure`** — GIVEN `--dsn` points to a bogus port WHEN invoked THEN exit code is non-zero AND stderr mentions the connection failure.

##### Idempotency

- **`test_apply_is_idempotent_against_an_existing_schema`** — GIVEN the fixture SQL has been applied once WHEN applied a second time THEN exit code is 0 AND the `foo` table still exists (uses `CREATE TABLE IF NOT EXISTS`).
- **`test_apply_does_not_drop_existing_data`** — GIVEN the schema is applied AND a row is inserted into `foo` WHEN the schema is applied again THEN the row still exists (no `DROP TABLE` in the apply).

##### Connection timeout

- **`test_apply_uses_five_second_timeout`** — GIVEN the DSN points to a port that accepts but never responds WHEN invoked THEN the script exits with a connection timeout error within ~6 seconds (5s timeout + small overhead).
- **`test_apply_uses_psycopg_3_not_psycopg2`** — GIVEN the apply script imports its DB driver WHEN inspected THEN the import is `psycopg` (the locked driver from F1.1), not `psycopg2` or `pg8000`.

##### Multi-file apply

- **`test_apply_can_run_multiple_sql_files_in_order`** — GIVEN two SQL files passed on the command line WHEN invoked THEN both are applied AND tables from the second file reference the first file's tables (FK works).

### Implementation Notes

- **Layer(s):** operational tooling
- **Pattern reference:** `scripts/verify_build.py` for the "script reads env, connects with timeout, prints PASS/FAIL, exits with code" pattern. `tests/conftest.py` doesn't exist yet; create it.
- **Key decisions:**
  - Decision 1 (plan): Raw SQL + apply script (no Alembic).
  - Decision 3 (plan): All `TIMESTAMPTZ` everywhere — apply script doesn't transform; that's the migration's job.
  - Decision 10 (plan): Forward-only — no `DROP TABLE` in the apply path.
  - Decision from the apply script behavior section: read file as a string, execute verbatim, no interpolation.
- **Libraries:** `psycopg[binary]` (already in `pyproject.toml`); Python stdlib (`argparse`, `os`, `sys`).
- **Python version:** `>=3.11.0,<3.12` (matches `pyproject.toml`).
- **Test fixture pattern:** session-scoped pytest fixture that creates a fresh DB once per test session, all tests share it; teardown drops the DB. The fixture uses a separate `unifleet_test_<uuid>` database name to avoid colliding with the dev DB.

### Scope Boundaries

- DO NOT include any app-specific DDL in this task. The fixture SQL is `CREATE TABLE foo (id INT)` — the barest possible smoke test.
- DO NOT add migration logic. F2.5 is the data migration task. This task is "apply SQL to a Postgres."
- DO NOT add a `--strict` flag that checks for schema drift. Plan says it's a follow-up.
- DO NOT write a CLI framework (Click, Typer, etc.). `argparse` is enough.
- DO NOT change `main.py`, `persistence.py`, `price_store.py`, `discount_store.py`, or any other app code. This task is purely operational tooling.
- Only: write `db/apply.py` + `tests/test_apply.py` + the `postgres_db` fixture in `tests/conftest.py`.

### Files Expected

**New files:**
- `db/apply.py` — the script
- `tests/test_apply.py` — the tests
- `tests/conftest.py` — shared `postgres_db` fixture

**Modified files:**
- None in `db/` or `app/` code. (The `tests/conftest.py` is a new file in this task, not a modification of an existing one — there is no `conftest.py` yet.)

**Must NOT modify:**
- `main.py` (reason: F1.1 is platform-only; F2.1 is schema-only; app code is F2.2+)
- `persistence.py`, `models.py`, `price_store.py`, `discount_store.py` (reason: app code stays on CSV until F2.2 wires the Postgres backend)
- `pyproject.toml` (reason: no new deps; `psycopg[binary]` already added in F1.1)
- `docker-compose.yml` (reason: the local Docker Postgres is already running from the F1.5 setup; the apply script just connects to it)

### TDD Sequence

1. **Red** — Write `tests/test_apply.py` with the 10 tests above. Run `poetry run pytest tests/test_apply.py` and confirm they fail with `ModuleNotFoundError: No module named 'db'` (no `db/__init__.py`, no `db/apply.py`).
2. **Green** — Write `db/__init__.py` (empty marker) and `db/apply.py` (the script). Use `unittest.mock` to mock the connect call for the connection-failure and timeout tests. Confirm 10/10 green.
3. **Refactor** — Extract repeated setup (e.g., the path-to-fixture-SQL helper) into a fixture. Confirm 10/10 still green.

---

## Task T2: `db/schema.sql` — the 9-table schema

> **Status:** done
> **Effort:** m
> **Priority:** critical
> **Depends on:** T1

### Description

Write `db/schema.sql` defining all 9 tables: `vouchers` (wide, 28+ columns), `stations`, `customers`, `presets`, `prices`, `price_history`, `discounts`, `discount_history`, `audit_log`. Indexes, FK constraints, and `TIMESTAMPTZ`/`NUMERIC` typing per the plan. Tested via the T1 apply script against a real Postgres. F2.2 (PostgresRepo) is unblocked once this lands.

### Test Plan

#### Test File(s)
- `tests/test_schema.py`

#### Test Scenarios

##### Table existence

- **`test_apply_creates_all_nine_tables`** — GIVEN `db/schema.sql` is applied to a fresh database WHEN the test queries `information_schema.tables` THEN every expected table name is present: `vouchers`, `stations`, `customers`, `presets`, `prices`, `price_history`, `discounts`, `discount_history`, `audit_log`.

##### Vouchers: 28 columns

- **`test_vouchers_has_voucher_id_primary_key`** — GIVEN the schema is applied WHEN the test queries `information_schema.table_constraints` AND `key_column_usage` for table `vouchers` THEN `voucher_id` is the PRIMARY KEY.
- **`test_vouchers_has_all_28_voucher_columns`** — GIVEN the schema is applied WHEN the test queries columns of `vouchers` THEN every name in `models.VOUCHER_COLUMNS` exists (28 names: `voucher_id`, `station`, `requested_amount_php`, `liters_requested`, `transaction_date`, `expected_refill_date`, `live_price_php_per_liter`, `discount_per_liter`, `discount_total`, `total_dispensed`, `liters_dispensed`, `driver_name`, `vehicle_plate`, `truck_make`, `truck_model`, `number_of_wheels`, `status`, `redemption_timestamp`, `created_at`, `updated_at`, `price_snapshot_php_per_liter`, `price_snapshot_updated_at`, `discount_snapshot_php_per_liter`, `discount_snapshot_captured_at`, `discount_total_php`, `total_dispensed_php`, `computed_at`).
- **`test_vouchers_status_has_default_unverified`** — GIVEN the schema is applied WHEN the test queries `information_schema.columns` for `vouchers.status` THEN `column_default` is `'Unverified'`.
- **`test_vouchers_money_columns_are_numeric`** — GIVEN the schema is applied WHEN the test queries column data types for `requested_amount_php`, `live_price_php_per_liter`, `discount_per_liter`, `discount_total`, `total_dispensed`, `price_snapshot_php_per_liter`, `discount_snapshot_php_per_liter`, `discount_total_php`, `total_dispensed_php` THEN all are `NUMERIC` (not `FLOAT`, not `REAL`, not `DOUBLE PRECISION`).
- **`test_vouchers_timestamp_columns_are_timestamptz`** — GIVEN the schema is applied WHEN the test queries column data types for `transaction_date`, `expected_refill_date`, `redemption_timestamp`, `created_at`, `updated_at`, `price_snapshot_updated_at`, `discount_snapshot_captured_at`, `computed_at` THEN all are `timestamp with time zone`.

##### Foreign keys

- **`test_vouchers_station_id_fk_to_stations`** — GIVEN the schema is applied WHEN the test queries `information_schema.referential_constraints` THEN there is an FK from `vouchers.station_id` to `stations.id`.
- **`test_vouchers_account_code_fk_to_customers`** — Same pattern: FK from `vouchers.account_code` to `customers.account_code`.
- **`test_audit_log_voucher_id_fk_to_vouchers`** — Same pattern: FK from `audit_log.voucher_id` to `vouchers.voucher_id`.
- **`test_presets_account_code_fk_to_customers`** — Same pattern: FK from `presets.account_code` to `customers.account_code`.
- **`test_prices_station_id_fk_to_stations`** — Same pattern: FK from `prices.station_id` to `stations.id`.
- **`test_discounts_station_id_fk_to_stations`** — Same pattern: FK from `discounts.station_id` to `stations.id`.
- **`test_price_history_station_id_fk_to_stations`** — Same pattern: FK from `price_history.station_id` to `stations.id`.
- **`test_discount_history_station_id_fk_to_stations`** — Same pattern: FK from `discount_history.station_id` to `stations.id`.

##### Indexes

- **`test_vouchers_status_indexed`** — GIVEN the schema is applied WHEN the test queries `pg_indexes` for `vouchers` THEN a non-PK index on `status` exists.
- **`test_vouchers_transaction_date_indexed`** — Same pattern: non-PK index on `transaction_date`.
- **`test_vouchers_created_at_indexed`** — Same pattern: non-PK index on `created_at`.
- **`test_audit_log_timestamp_indexed`** — Same pattern: non-PK index on `audit_log.timestamp`.

##### Stations specifics

- **`test_stations_id_is_varchar_primary_key`** — GIVEN the schema is applied WHEN the test queries `information_schema.columns` for `stations.id` THEN `data_type` is `character varying` AND `character_maximum_length` is 64 AND it is the PRIMARY KEY.
- **`test_stations_legacy_id_is_unique`** — GIVEN the schema is applied WHEN the test queries `information_schema.table_constraints` for `stations` THEN there is a UNIQUE constraint on `legacy_id`.
- **`test_stations_is_active_defaults_true`** — GIVEN the schema is applied WHEN the test queries `information_schema.columns` for `stations.is_active` THEN `column_default` is `true`.

##### Audit log specifics

- **`test_audit_log_id_is_bigserial`** — GIVEN the schema is applied WHEN the test queries `information_schema.columns` for `audit_log.id` THEN `data_type` is `bigint` AND `is_identity` is `YES` (Postgres 10+ identity column syntax).

##### Customers specifics

- **`test_customers_account_code_is_primary_key`** — Same pattern as `vouchers.voucher_id`: VARCHAR(16) PK.

##### Presets specifics

- **`test_presets_unique_on_account_code_driver_name`** — GIVEN the schema is applied WHEN the test queries `information_schema.table_constraints` for `presets` THEN a UNIQUE constraint on `(account_code, driver_name)` exists.

##### Idempotency (re-apply)

- **`test_schema_apply_is_idempotent`** — GIVEN the schema is applied once WHEN applied again THEN no error AND all tables still present.

### Implementation Notes

- **Layer(s):** schema design
- **Pattern reference:** `models.py:42-62` for the existing `SCHEMA_SQL` (the SQLite legacy — F2.1 replaces this). `VOUCHER_COLUMNS` at `models.py:3-37` for the authoritative voucher column list.
- **Key decisions:** all 12 from the plan's Decisions Log apply here.
- **Libraries:** none new; pure SQL.
- **Apply mechanism:** use the T1 `db/apply.py` (e.g., `python -m db.apply db/schema.sql`).
- **Test fixture reuse:** the `postgres_db` fixture from T1's `tests/conftest.py` provides the DSN. This task adds a `schema_db` fixture that applies `db/schema.sql` once per test (or session — both work, session is faster).

### Scope Boundaries

- DO NOT add `CHECK` constraints on `audit_log.action` or `vouchers.status`. Plan says F3.x is the right time.
- DO NOT add `BEFORE UPDATE OR DELETE` triggers on the append-only tables. Plan says F2.5 territory.
- DO NOT add a `BEFORE UPDATE` trigger that auto-bumps `updated_at`. F2.2 territory.
- DO NOT change the column types from what's in the plan (NUMERIC, TIMESTAMPTZ, VARCHAR with the exact lengths). F2.5 will normalize the data; the schema is locked.
- DO NOT seed any data in this task. T3 owns the seed data.
- DO NOT modify the existing `models.py:42-62` `SCHEMA_SQL` (the SQLite one). That's a F2.2 cleanup.
- Only: write `db/schema.sql` + `tests/test_schema.py`.

### Files Expected

**New files:**
- `db/schema.sql` — the DDL
- `tests/test_schema.py` — the tests

**Modified files:**
- None. The `tests/conftest.py` from T1 already provides the `postgres_db` fixture; this task adds a `schema_db` fixture or extends `postgres_db` to apply `db/schema.sql` automatically.

**Must NOT modify:**
- `db/apply.py` (T1's output; this task consumes it)
- `main.py`, `persistence.py`, `models.py`, `price_store.py`, `discount_store.py` (F2.1 is schema-only; F2.2+ touches app code)
- `pyproject.toml` (no new deps)

### TDD Sequence

1. **Red** — Write `tests/test_schema.py` with all 25+ tests. Run `poetry run pytest tests/test_schema.py` and confirm they fail with `FileNotFoundError: db/schema.sql` (or `psycopg.errors.UndefinedTable` for tables that don't exist yet).
2. **Green** — Write `db/schema.sql` with the 9 tables, indexes, FKs, defaults per the plan. Confirm all tests pass.
3. **Refactor** — If any test is redundant (e.g., the per-table FK tests could be a single loop), consolidate. Re-run tests to confirm green.

---

## Task T3: `db/seed_*.sql` — station and price seed data

> **Status:** done
> **Effort:** s
> **Priority:** high
> **Depends on:** T1, T2

### Description

Write the seed SQL files (`db/seed_stations.sql`, `db/seed_prices.sql`) that populate the `stations` and `prices` tables from the existing data sources: the 10 stations in `price_store.py:_DEFAULT_STATIONS` and the 10 rows in `data/stations.csv`. Extend `db/apply.py` (or add a new entry point) to apply the seeds after the schema. Idempotent: re-running must not duplicate rows.

### Test Plan

#### Test File(s)
- `tests/test_seeds.py`

#### Test Scenarios

##### Stations seed

- **`test_seeds_populate_stations_from_default_stations`** — GIVEN the schema is applied AND the seeds are applied WHEN the test counts rows in `stations` THEN at least 10 rows are present AND every slug ID from `price_store._DEFAULT_STATIONS` (`cleanfuel_valenzuela`, `unioil_mandaluyong`, `seaoil_bicutan`, `ecooil_qc`, `maximumfuel_val`, `phoenix_meyc`, `petro_gsanj`, `gazz_binan`, `filoil_stamesa`, `petron_port`) exists.
- **`test_seeds_populate_stations_from_csv`** — GIVEN the schema is applied AND the seeds are applied WHEN the test counts rows in `stations` AND the test queries for stations matching `data/stations.csv`'s names (e.g., `EcoOil - EDSA Mandaluyong`, `EcoOil - QC`, `EcoOil - Pasay`, etc.) THEN at least 5 of the 9 CSV stations are present as `display_name` values.
- **`test_each_station_has_a_brand_and_display_name`** — GIVEN the seeds are applied WHEN the test queries for stations with `brand IS NULL` OR `display_name IS NULL` THEN the result is empty (all 10+ rows have brand and display_name set).

##### Prices seed

- **`test_seeds_populate_prices_for_each_default_station`** — GIVEN the schema is applied AND the seeds are applied WHEN the test counts rows in `prices` THEN at least 10 rows are present AND each row's `price_php_per_liter` is between 0 and 200 (the `price_store.set_price` validation range).
- **`test_each_default_station_has_a_price_row`** — GIVEN the seeds are applied WHEN the test JOINs `stations` and `prices` ON `stations.id = prices.station_id` THEN every default-station slug is present (1:1 coverage).
- **`test_prices_have_realistic_values`** — GIVEN the seeds are applied WHEN the test queries for prices outside the 30.0 - 200.0 PHP range THEN the result is empty (sanity check; the defaults are 57-60 PHP).

##### Idempotency

- **`test_seeds_are_idempotent`** — GIVEN the seeds are applied once WHEN applied a second time THEN the row count in `stations` and `prices` does not double (uses `ON CONFLICT ... DO UPDATE` or `ON CONFLICT ... DO NOTHING`).
- **`test_apply_with_seeds_creates_no_extra_tables`** — GIVEN the seeds are applied WHEN the test counts tables in the database THEN only the 9 expected tables exist (seeds don't accidentally create temp/auxiliary tables).

### Implementation Notes

- **Layer(s):** seed data
- **Pattern reference:** `price_store.py:9-90` for `_DEFAULT_STATIONS` (the source of truth for the 10 default stations and prices). `data/stations.csv` for the 9 legacy station rows.
- **Key decisions:**
  - Slug IDs are the canonical PK; legacy int IDs are denormalized. Decision 3 in plan.
  - Seeds use `ON CONFLICT (id) DO UPDATE SET ...` so re-running is safe.
- **Libraries:** none new; pure SQL.
- **How to read `_DEFAULT_STATIONS` from SQL:** the seed file is hand-maintained (10 INSERT statements), not generated. A follow-up could generate it from the Python literal, but that's a build-time concern, not a runtime one.
- **How to read `data/stations.csv` from SQL:** use Postgres's `COPY ... FROM` with a hardcoded file path, OR use 9 hand-maintained INSERT statements. `COPY` is more correct (no drift between the file and the seed) but couples the seed to the dev's filesystem. INSERTs are simpler and the data is small (9 rows). Plan calls for INSERTs unless the file is also the source of truth at runtime.
- **Apply mechanism:** the same `db/apply.py` from T1. Extend it to accept multiple files: `python -m db.apply db/schema.sql db/seed_stations.sql db/seed_prices.sql`. The apply runs them in order, so schema before seeds.

### Scope Boundaries

- DO NOT seed `customers`, `presets`, `audit_log`, `price_history`, `discount_history`, or `discounts`. The plan says these have no seed data (customers is from CSV and F2.5 migrates; the others are runtime/empty). T3 only seeds `stations` and `prices`.
- DO NOT change `_DEFAULT_STATIONS` in `price_store.py`. The seed is a copy; the Python literal stays the source for F2.2's wrapper.
- DO NOT generate the seed SQL from Python at build time. Hand-maintained SQL is fine for 19 rows.
- DO NOT migrate the `data/customers.csv` or `data/presets/*.csv` content into the seed. F2.5 owns data migration; this task only seeds `stations` and `prices`.
- Only: write `db/seed_stations.sql` + `db/seed_prices.sql` + extend `db/apply.py` (or add a wrapper) + `tests/test_seeds.py`.

### Files Expected

**New files:**
- `db/seed_stations.sql` — INSERT statements for the ~19 stations
- `db/seed_prices.sql` — INSERT statements for the 10 default prices
- `tests/test_seeds.py` — the tests

**Modified files:**
- `db/apply.py` — extend to accept multiple files and run them in order (reason: T1's apply.py only handled a single file; the multi-file case is the natural way to apply schema + seeds in one command)

**Must NOT modify:**
- `db/schema.sql` (T2's output; this task consumes it)
- `price_store.py` (F2.3 will wrap it; this task only seeds the data)
- `main.py`, `persistence.py`, `models.py`, `discount_store.py` (F2.1+ tasks; F2.2+ touches app code)
- `pyproject.toml` (no new deps)

### TDD Sequence

1. **Red** — Write `tests/test_seeds.py` with the 8 tests above. Run `poetry run pytest tests/test_seeds.py` and confirm they fail with the table-is-empty errors (the seeds aren't applied yet).
2. **Green** — Write `db/seed_stations.sql` (19 INSERT statements: 10 from `_DEFAULT_STATIONS`, 9 from `data/stations.csv`) and `db/seed_prices.sql` (10 INSERT statements). Extend `db/apply.py` to take multiple files. Confirm 8/8 green.
3. **Refactor** — If the `db/apply.py` multi-file support can be a small refactor of the single-file code, do it. Confirm 8/8 still green.

---
