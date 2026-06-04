# F2.3 — Sidecar Stores → Postgres

> Replaces the JSON-backed `price_store.py` and `discount_store.py`
> with Postgres-backed implementations. The two modules become thin
> wrappers over the F2.1 schema (stations + prices + price_history,
> stations + discounts + discount_history). Public function/class
> signatures are preserved so call sites in main.py /
> generate_voucher.py do not change.

**Feature spec:** PROJECT-migrate-to-railway.md §Feature Map row F2.3
**Depends on:** F2.1 (schema), F2.2 (PostgresRepo + pool infra)
**Status:** in progress

## Mapping

| Legacy (JSON/CSV) | New (Postgres) |
|-------------------|----------------|
| `data/station_prices.json` | `stations` + `prices` (JOIN on station_id) |
| `data/discount_store.json` | `stations` + `discounts` (JOIN on station_id) |
| `data/discount_history.csv` | `discount_history` table |
| (none — no price history) | `price_history` table |

The `prices` table doesn't track changes between updates; the new
`price_history` table provides a proper append-only audit log (and
F2.5 migration backfills historical data).

`discount_history` columns in CSV → Postgres mapping:

| CSV column | Postgres column |
|------------|-----------------|
| `timestamp_iso` | `timestamp_iso` (TIMESTAMPTZ) |
| `station` (display_name) | `station_id` (FK lookup) |
| `old_discount_per_liter` | `old_discount_per_liter` (NUMERIC) |
| `new_discount_per_liter` | `new_discount_per_liter` (NUMERIC) |
| `actor` | `actor` (VARCHAR) |
| `reason` | `reason` (TEXT) |

`price_history` columns (no CSV counterpart, no historical data
to migrate):

| Column | Type | Source |
|--------|------|--------|
| `station_id` | VARCHAR(64) FK | caller |
| `old_price` | NUMERIC(10,4) | previous value (NULL on insert) |
| `new_price` | NUMERIC(10,4) | caller |
| `timestamp_iso` | TIMESTAMPTZ | NOW() |
| `timestamp_unix` | BIGINT | EXTRACT(EPOCH FROM NOW()) |
| `actor_ip` | VARCHAR(50) | caller (NEW: not in legacy) |
| `user_agent` | TEXT | caller (NEW) |

## Public APIs (preserved)

### `price_store.py` (module-level functions)
- `init_if_missing()` — no-op in PG (data is in DB; F2.1 seeds stations)
- `load_all()` → `{"stations": [...]}`
- `save_all(obj)` — back-compat: rejects writes (or no-ops with a warning)
- `list_stations()` → `List[Dict]` with keys: `id`, `brand`, `name`, `location`, `price_php_per_liter`, `updated_at` (epoch int)
- `get_station(station_id)` → `Optional[Dict]`
- `set_price(station_id, new_price)` → `Dict` (raises `ValueError` for out-of-range, `KeyError` for missing)
- `upsert_station(st)` → `Dict`

### `discount_store.py` (`DiscountStore` class)
- `DiscountStore()` constructor — takes optional DSN, opens a pool
- `get_all()` → `Dict[str, float]` keyed by display_name (not slug id)
- `get(station)` → `Optional[float]` (looks up by display_name)
- `set(station, value, actor, reason)` — updates `discounts` + appends to `discount_history`
- `set_many(updates, actor, reason)` — bulk upsert/remove
- `clear_all(actor, reason)` — remove all discounts, log to history
- `DiscountValueError` exception (preserved)

## Tasks

### T1 — pool + price_store + discount_store
> **Status:** done
> **Effort:** m
> **Priority:** high
> **Depends on:** F2.1, F2.2

Implement the shared `db/pool.py`, rewrite `price_store.py` and
`discount_store.py` to use Postgres. Per user request, no new tests
were added (the existing 97 tests already exercise the seed data;
F2.3 is a pure refactor of read/write paths). Verification: full
test suite still green (97/97), main.py imports cleanly, smoke test
end-to-end works.

**Deliverables:**
- `db/pool.py` (new) — `get_pool()` shared singleton
- `price_store.py` (rewrite, 240 lines) — PG-backed, public API preserved
- `discount_store.py` (rewrite, 280 lines) — PG-backed, public API preserved

**Smoke-verified end-to-end:** list_stations returns 19 rows in legacy
shape; set_price updates + appends to price_history; DiscountStore.set
upserts + appends to discount_history; get_all returns
{display_name: value} dict.

**Known behavior change:** DiscountStore.set() now raises KeyError for
unknown station names (was: silently created a new JSON entry). The
F2.5 data migration script must map legacy JSON keys to slug ids
before importing.

### T2 — verification + cleanup
> **Status:** not started
> **Effort:** s
> **Priority:** medium
> **Depends on:** T1

After T1 lands: confirm main.py imports without errors, full test
suite still green, identify any orphaned data files for archival.

**Deliverables:** (depends on what T1 surfaces)
