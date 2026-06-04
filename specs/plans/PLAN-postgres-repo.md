# F2.2 — Complete `PostgresRepo`

> Implements the full `Repo` interface from `persistence.py` against the
> Postgres schema built in F2.1. Replaces the SQLite-backed `DBRepo`
> (which is incomplete — only 5 of 7 methods implemented). After F2.2
> lands, `get_repo(backend='pg')` returns a fully functional repo; the
> Phase-2 cleanup in F2.x will swap the default backend from `csv` to
> `pg` and delete `CSVRepo`.

**Feature spec:** PROJECT-migrate-to-railway.md §Feature Map row F2.2
**Depends on:** F2.1 (schema) — done
**Status:** in progress

## Interface contract

The 7 methods on `Repo` (per `persistence.py:CSVRepo`):

| # | Method | Returns | Notes |
|---|--------|---------|-------|
| 1 | `list_recent_vouchers(limit=50)` | `List[Dict]` | Sorted: has-transaction_date first, date desc, append order desc |
| 2 | `list_all_vouchers()` | `List[Dict]` | All rows, no order guarantee required |
| 3 | `get_voucher(voucher_id)` | `Optional[Dict]` | None if not found |
| 4 | `set_status(voucher_id, new_status, redemption_timestamp)` | None | `redemption_timestamp=''` ⇒ store as NULL in Postgres |
| 5 | `append_vouchers(rows)` | None | UPSERT (ON CONFLICT DO UPDATE) |
| 6 | `update_voucher_fields(voucher_id, fields)` | None | Dynamic column update; bump `updated_at`; mirror `*_php` → legacy cols |
| 7 | `create_unverified_booking(data)` | `Dict` | Returns the created row including `voucher_id` |

## Type-handling decisions

- Empty string `""` from CSV-world inputs → `NULL` in Postgres for nullable columns.
- Empty string for `VARCHAR NOT NULL` columns (none in `vouchers` except `voucher_id` PK and `status`) → use the column default.
- `NUMERIC` columns accept `float` or `Decimal` from callers; pass through as-is to psycopg.
- `TIMESTAMPTZ` columns accept `datetime` (with tz) or `str` (ISO 8601); empty string → NULL.
- `BOOL` (none in `vouchers` — FKs are VARCHAR).

## Tasks

### T1 — pool + basic CRUD (3 reads + 2 simple writes)
> **Status:** done
> **Effort:** m
> **Priority:** high
> **Depends on:** F2.1 (schema)

Add `psycopg-pool` dep; create `db/postgres_repo.py` with a `PostgresRepo`
class that owns a `psycopg_pool.ConnectionPool`; implement 5 basic CRUD
methods so tests can round-trip:
- 3 reads: `list_recent_vouchers`, `list_all_vouchers`, `get_voucher`
- 2 simple writes: `set_status`, `append_vouchers` (UPSERT)
Write round-trip tests in `tests/test_postgres_repo.py`.

**Deliverables:**
- `pyproject.toml` — add `psycopg-pool = "^3.2"`
- `poetry.lock` — regenerated
- `db/postgres_repo.py` — class with pool + 5 methods
- `tests/test_postgres_repo.py` — 18 tests for the 5 methods

### T2 — complex writes + get_repo wiring
> **Status:** done
> **Effort:** s
> **Priority:** high
> **Depends on:** T1

Implement `create_unverified_booking` and `update_voucher_fields` (the
two methods DBRepo never had). Wire `get_repo(backend='pg' or
'postgres')` in `persistence.py` to instantiate `PostgresRepo`. Add
tests for all of the above.

**Deliverables:**
- `db/postgres_repo.py` — `create_unverified_booking`, `update_voucher_fields`
- `persistence.py` — `get_repo` dispatches 'pg'/'postgres' to `PostgresRepo`
- `tests/test_persistence.py` — 6 tests for the dispatcher
- `tests/test_postgres_repo.py` — 11 new tests for the 2 new methods
- `docker-compose.test.yml` — bind-mount full repo (so source edits
  are picked up without rebuilding the image)

**Test count:** 11 + 6 = 17

### T3 — integration test
> **Status:** done
> **Effort:** s
> **Priority:** high
> **Depends on:** T2

End-to-end test that walks the full voucher lifecycle through a
single `PostgresRepo` instance. Catches cross-method interaction
bugs that the per-method tests miss.

**Deliverable:** `tests/test_postgres_repo_integration.py` (5 tests)

**Test count:** 5 (full lifecycle, list_recent sees new voucher,
redeem-then-unredeem clears timestamp, never-existed returns None,
unknown column is ignored)
