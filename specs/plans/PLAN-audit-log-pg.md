# F2.4 — Ops Audit Log → Postgres

> Replaces the CSV-backed `append_audit()` function in main.py
> (`data/ops_audit_log.csv`) with an append-only insert into the
> F2.1 schema's `audit_log` table. Preserves the public function
> signature so the 5 call sites in main.py do not change.

**Feature spec:** PROJECT-migrate-to-railway.md §Feature Map row F2.4
**Depends on:** F2.1 (audit_log table), F2.3 (shared pool)
**Status:** done

## Mapping (CSV → Postgres)

| CSV column (`data/ops_audit_log.csv`) | PG column (`audit_log`) | Notes |
|--------------------------------------|-------------------------|-------|
| `timestamp` (ISO local, no tz) | `timestamp` (TIMESTAMPTZ) | Set to `NOW()` at INSERT |
| `action` | `action` (VARCHAR(50) NOT NULL) | Same |
| `voucher_id` | `voucher_id` (VARCHAR(32) FK) | Empty / None → NULL (FK is nullable) |
| `from_status` | `from_status` (VARCHAR(50)) | Empty string → NULL |
| `to_status` | `to_status` (VARCHAR(50)) | Empty string → NULL |
| `route` | `route` (VARCHAR(200)) | Truncated to 200 chars |
| `actor_ip` | `actor_ip` (VARCHAR(50)) | Truncated to 50 chars |
| `user_agent` | `user_agent` (TEXT) | Same |
| `note` | `note` (TEXT) | Empty string → NULL |

## Call sites (preserved)

The 5 call sites in main.py use:
```
append_audit(action, voucher_id, from_status, to_status, note)
```
…and rely on the function reading `request.path`, `request.remote_addr`,
`request.headers.get("X-Forwarded-For")`, `request.headers.get("User-Agent")`.

The new `audit_log.append_audit()` does the same — imports `flask.request`
directly. Must be called from within a Flask request context (the only
place main.py calls it).

## Tasks

### T1 — `audit_log.py` + main.py update
> **Status:** done
> **Effort:** s
> **Priority:** high
> **Depends on:** F2.1, F2.3

Create `audit_log.py` with `append_audit()` that inserts into
`audit_log` via the shared `db/pool.py` pool. Remove the CSV
implementation and the `AUDIT_PATH` / `AUDIT_FIELDS` constants
from main.py. Add the import.

**Deliverables:**
- `audit_log.py` (new, ~80 lines) — module-level `append_audit()`
- `main.py` — remove old `append_audit` (was 20 lines), remove
  `AUDIT_PATH` / `AUDIT_FIELDS` constants, add `from audit_log
  import append_audit`

**Per user request, no new pytest tests** (F2.3 pattern: implement
+ smoke-test only). Verified via:
- Full pytest suite: 97/97 pass (~14s), no regressions
- End-to-end smoke: 3 scenarios (real data, empty→NULL, None→NULL)
  all insert correctly into `audit_log` table

### T2 — plan doc + commit
> **Status:** done
> **Effort:** s
> **Priority:** medium

Write the F2.4 plan doc and commit + push.

## Known behavior change

- `data/ops_audit_log.csv` is now dead (no longer read or written).
  The file is left in place for one cycle in case of rollback.
- If a caller passes a `voucher_id` that does not exist in the
  `vouchers` table, the INSERT fails with a FK violation. The
  `try/except` in `append_audit` swallows the error and logs to
  stderr (matches the legacy "audit never breaks the request"
  guarantee). F2.5 will backfill historical rows from the CSV.

## Verification

- `pytest` full suite: **97/97 GREEN** (no regressions)
- Smoke test:
  - 3 `append_audit` calls in a Flask `test_request_context`
  - All 3 rows present in `audit_log` with correct values
  - Empty `from_status` / `to_status` / `note` → NULL in DB
  - `None` voucher_id → NULL in DB
  - `timestamp` is TIMESTAMPTZ with tzinfo
  - `route`, `actor_ip`, `user_agent` captured from request
