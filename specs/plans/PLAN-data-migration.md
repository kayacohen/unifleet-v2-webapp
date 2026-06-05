# F2.5 — Migrate Legacy CSV/JSON Data → Postgres

> One-shot backfill from `data/*.csv|json` into the F2.1 schema.
> Sources: `data/stations.csv`, `data/station_prices.json`,
> `price_store._DEFAULT_STATIONS`, `data/customers.csv`,
> `data/ops_audit_log.csv`. Target: `stations`, `prices`,
> `customers`, `audit_log` tables.

**Feature spec:** PROJECT-migrate-to-railway.md §Feature Map row F2.5
**Depends on:** F2.1 (schema), F2.3 (`db/pool.py`), F2.4 (audit_log)
**Status:** done

## Scope

| Source | Rows | Target | Strategy |
|--------|------|--------|----------|
| `data/stations.csv` | 10 | `stations` | UPSERT (id, brand, display_name, location, legacy_id) |
| `price_store._DEFAULT_STATIONS` | 10 | `stations` | UPSERT (id, brand, display_name) — fills the canonical 10 |
| `data/station_prices.json` | 10 | `prices` | UPSERT (station_id, price_php_per_liter, updated_at=now) |
| `data/customers.csv` | 9 | `customers` | Dedup by `account_code` (keep last), UPSERT |
| `data/ops_audit_log.csv` | 48 | `audit_log` | TRUNCATE + INSERT (one-time backfill) |
| `data/requested_vouchers.csv` | 4 | _not migrated_ | Per plan: pending booking requests are not historical data |

## Idempotency

- **stations / prices / customers**: `INSERT … ON CONFLICT … DO UPDATE`.
  Re-running is a no-op (delta=0).
- **audit_log**: `TRUNCATE audit_log` then bulk `INSERT`. Re-running
  produces the same final state (delta=0 on `COUNT(*)`).
  - Acceptable because `audit_log` is append-only from the live app
    and this script is a one-time backfill, not a continuous sync.
  - Live `append_audit()` calls in production (post-deploy) will
    append fresh rows after migration.

## Anomaly handling

| Anomaly | Resolution |
|---------|------------|
| Duplicate `account_code` in `customers.csv` | Keep LAST occurrence, log anomaly |
| `audit_log.voucher_id` references nonexistent voucher | Set `voucher_id=NULL`, log anomaly |
| Unknown | Log + skip |

The 27 `UF-2025…` and `UF2025080218320000` voucher_ids in
`ops_audit_log.csv` are pre-voucher-conversion booking IDs that
were never converted to vouchers, so they have no FK target. They
are stored with `voucher_id=NULL` (audit_log.voucher_id is nullable).

## Tasks

### T1 — `scripts/migrate_to_postgres.py`
> **Status:** done
> **Effort:** m
> **Priority:** high
> **Depends on:** F2.1, F2.3, F2.4

One-shot migration script. ~360 lines, no class — sequence of:
1. Read all source files (CSV / JSON / Python constant)
2. Connect via psycopg (DSN from `--dsn` or `DATABASE_URL` / `UNIFLEET_DB_DSN`)
3. UPSERT stations / prices / customers; TRUNCATE + INSERT audit_log
4. Run 4 invariants against the final DB state
5. Write JSON report to `--report-out`

**Deliverables:**
- `scripts/migrate_to_postgres.py` (new, ~360 lines)
- `sys.path` injection: `sys.path.insert(0, repo_root)` so the
  `from price_store import _DEFAULT_STATIONS` import works
  regardless of CWD

**Per user request, no new pytest tests** (F2.3 / F2.4 pattern:
implement + smoke-test only). Verified via:
- `make test-db`: 97/97 pass (~15s), no regressions
- End-to-end smoke: 19 stations, 10 prices, 8 customers, 48
  audit_log rows; 4/4 invariants pass; idempotent (re-run = delta=0)

### T2 — plan doc + commit
> **Status:** done
> **Effort:** s
> **Priority:** medium

Write the F2.5 plan doc and commit + push.

## Invariants checked

1. **Every `prices` row has a matching `stations` row** (FK already
   enforces this; the invariant catches any post-migration drift)
2. **Every `customer.account_code` is unique** (UNIQUE constraint
   enforces; the invariant catches the dedup outcome)
3. **Every `audit_log` row has a non-empty `action`** (NOT NULL
   enforces; the invariant catches empty-string edge cases)
4. **Every `audit_log.voucher_id` is either NULL or exists in
   `vouchers`** (FK enforces; the invariant catches orphans not
   caught by the migration script)

## Verification

- **Migration output** (post-run, 2nd run for idempotency):
  ```
  stations: db_before=19 db_after=19 delta=0
  customers: db_before=8  db_after=8  delta=0
  audit_log: db_before=48 db_after=48 delta=0
  anomalies: 28  (1 customers dedup + 27 audit_log missing FKs)
  invariant fails: 0
  MIGRATION OK
  ```
- **Direct DB counts**: 19 stations, 10 prices, 8 customers,
  48 audit_log rows
- **Smoke test**:
  - `price_store.list_stations()` returns 19 rows from PG
  - `price_store.load_all()` returns 10 prices with values
  - `DiscountStore().get_all()` returns 0 rows (no historical discounts)
  - `append_audit()` inserts correctly into `audit_log`
- **Report JSON** at `/tmp/migration_report.json`:
  ```json
  {
    "timestamp": "...",
    "dsn_redacted": "postgresql://unifleet:***@db:5432/unifleet",
    "data_dir": "data",
    "sources": { "data/stations.csv": 10, ... },
    "results": { "stations": {...}, "customers": {...}, ... },
    "invariants": [ { "name": "...", "passed": true }, ... ],
    "all_invariants_passed": true
  }
  ```

## Known behavior

- **Stations: F2.1 seed overlaps with F2.5 source.** The F2.1 T3
  seed already inserted 19 stations + 10 prices (1 with value,
  9 NULL). The F2.5 migration runs `ON CONFLICT DO UPDATE`, so
  the 9 NULL-priced stations remain NULL-priced (station_prices.json
  only has 10 entries, none of which are CSV-only). The 1 priced
  station (ecooil_qc) is updated to 58.30 (same value).
- **Audit log truncated before re-insert.** Acceptable for
  one-time backfill. If a fresh `append_audit()` row exists in
  the DB at re-run time, it will be lost. The script is intended
  to run once.
- **`requested_vouchers.csv` is ignored.** Per the F2.5 plan,
  pending booking requests are not historical data; they remain
  in the CSV for the operator to review post-migration.
- **Data quality**: `audit_log` has 27 rows with NULL `voucher_id`
  (orphaned booking IDs from pre-voucher-conversion era). These
  are real audit events; the NULL FK is correct.

## Open follow-ups

- F2.6: file asset pipeline on Railway Volume
- Phase 2 cleanup: delete `data/ops_audit_log.csv`,
  `data/stations.csv`, `data/customers.csv`,
  `data/station_prices.json` after Railway cutover is verified
