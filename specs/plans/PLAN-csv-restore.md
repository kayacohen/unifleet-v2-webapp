# PLAN — CSV/JSON → Postgres restore tool

> Idempotent re-sync of the live `data/*.csv` and `data/*.json`
> sources into the Postgres tables they were originally
> backfilled from in F2.5. Safe to run any time the CSVs change
> (or to bootstrap a fresh DB). Captures the rules and gotchas
> the script encodes so future operators can run it without
> re-deriving them.

**Feature spec:** `specs/plans/ROADMAP-post-f2.md` item #3
**Depends on:** F2.5 (PG schema, `data/legacy/` layout, voucher_id format)
**Status:** done
**Commit:** `86acc69` on `feature/F2.5` (now on `main`)

## Why this exists

`data/` is gitignored for live-runtime files (`master_vouchers.csv`,
`ops_audit_log.csv`, `station_prices.json`, …) but a few entries
are allow-listed (`data/stations.csv`, `data/customers.csv`,
`data/requested_vouchers.csv`). The rest drift independently of
PG: CSVs can be edited by hand, restored from a backup, or
uploaded via a future operator workflow. When the CSVs and PG
diverge, this script brings PG back in line — either as a
one-shot bootstrap (fresh DB) or as an additive sync (existing
DB with new CSV rows).

`scripts/migrate_to_postgres.py` (F2.5) was the original
TRUNCATE+INSERT backfill. It is destructive (wipes live audit
rows) and not idempotent in the additive sense. This script is
its non-destructive sibling.

## Sources & destinations

| Source file | PG table | Strategy |
|---|---|---|
| `data/stations.csv` | `stations` | UPSERT by `id` (slug) **or** `legacy_id` |
| `data/station_prices.json` | `prices` | UPSERT by `station_id` |
| `data/customers.csv` | `customers` | UPSERT by `account_code`, dedup last-write-wins |
| `data/ops_audit_log.csv` | `audit_log` | delta-aware INSERT, skip on (action, route, timestamp, voucher_id) match |
| `data/requested_vouchers.csv` | `vouchers` | INSERT as `status='Unverified'`, deterministic `voucher_id` |

The script intentionally does **not** touch:
- `data/master_vouchers.csv` (gitignored, not in repo)
- `data/unifleet.db` (legacy SQLite, not part of the live flow)
- `data/presets/*.csv` (per-customer runtime files)
- `data/legacy/*` (read-only archive, the script does not write to it)

## Idempotency model

| Table | How re-runs are no-ops |
|---|---|
| `stations` | UPSERT matches on `id` or `legacy_id`. Refresh in place (display_name, brand, legacy_id). |
| `prices` | UPSERT matches on `station_id`. Refresh `price_php_per_liter` and bump `updated_at`. |
| `customers` | UPSERT matches on `account_code`. Refresh all 8 columns. CSV dup keys → last-write-wins (dict overwrite). |
| `audit_log` | Pre-check exists on `(action, route, timestamp, voucher_id)`; skip if present. New rows from the web app are preserved across re-runs. |
| `vouchers` (from requested) | Deterministic `voucher_id` (see below). Re-run finds the row by id and skips. |

Verified re-run on a fully-migrated DB: `inserted: 0` across all
5 steps. The only output is the per-step status line.

## Deterministic `voucher_id` (for `requested_vouchers.csv` → `vouchers`)

The CSV `data/requested_vouchers.csv` is a booking form dump
with no `voucher_id` column. To turn each row into an
`Unverified` voucher without colliding on re-run, the script
generates the id from the row content:

```python
date_part = refuel_dt[:10].replace("-", "")           # "2025-08-04T15:52" → "20250804"
h = md5(f"{account}|{plate}|{refuel_dt}|{amount}".encode()).hexdigest()[:5].upper()
voucher_id = f"UF-{date_part}-{h}"                   # e.g. "UF-20250804-2F24E"
```

Re-runs produce the same id → script skips the INSERT. Two
CSV rows with identical `(account, plate, refuel_dt, amount)`
hash to the same id; the first INSERT wins, the rest are
counted as `skipped`. This matches the semantic "same booking
request submitted twice = same voucher" — a deduplication, not
a bug.

## Orphan handling (for `audit_log`)

The CSV's historic `voucher_id` values (e.g.
`UF2025080218320000` — the 18-char pre-F2.5 format) reference
vouchers that don't exist in PG. The F2.5 backfill handled this
by setting `voucher_id=NULL` and logging an anomaly. This
script preserves the same behavior:

1. Pre-load `SELECT voucher_id FROM vouchers` into a Python set
2. For each CSV audit row, if its `voucher_id` is not in the
   set, set it to `None` and increment `orphan_voucher_id`
3. INSERT (now with NULL FK, no constraint violation)

The `orphan_voucher_id` counter is informational. With PG at
3 vouchers (the 3 `requested_vouchers.csv`-derived Unverified
rows) and the CSV at 48 historic rows, all 48 are flagged as
orphans. Re-runs preserve the smoke_test_f25 row + the 48 F2.5
backfilled rows + any new rows the web app added in between.

## Gotchas the script handles (and the bugs that taught us)

The commit landed with 6 fixes made during dev. These are
genuine foot-guns — re-read this section before editing the
script.

### 1. `legacy_id` is `varchar(64)`, not int

`stations.legacy_id` is a string in PG. The CSV column is
"1"–"10" but the script must not cast to int; the `id = %s OR
legacy_id = %s` lookup passes both as strings.

```python
# Wrong:
cur.execute("...OR legacy_id = %s::int", (legacy_id,))   # 'varchar = integer' error
# Right:
cur.execute("...OR legacy_id = %s", (legacy_id,))       # both strings
```

### 2. `(voucher_id IS NULL AND %s IS NULL)` needs `::text` cast

psycopg can't infer the type of a `None` parameter, so the
delta-aware audit_log existence check throws
`IndeterminateDatatype`. Workaround:

```sql
WHERE ((voucher_id IS NULL AND %s::text IS NULL) OR voucher_id = %s)
```

### 3. `slugify` must replace internal whitespace, not just dashes

The first version of `slugify` did `re.sub(r"\s*[-–]\s*", "_", s)`
then `re.sub(r"[^a-z0-9_]", "", s)`. This dropped the space
between "EDSA" and "Mandaluyong" (no dash, so the first regex
skipped it, and the second removed it without a replacement).
Result: `slugify("EcoOil - EDSA Mandaluyong")` →
`"ecooil_edsamandaluyong"` (wrong) instead of
`"ecooil_edsa_mandaluyong"`.

Fix: insert an explicit whitespace→underscore step.

```python
def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\s*[-–]\s*", "_", s)   # " - " or " – " → "_"
    s = re.sub(r"\s+", "_", s)          # any remaining whitespace → "_"
    s = re.sub(r"[^a-z0-9_]", "", s)    # drop other punctuation
    s = re.sub(r"_+", "_", s).strip("_")
    return s
```

Tested on all 19 station names in the current dataset.

### 4. CSV BOMs

`data/ops_audit_log.csv` and `data/customers.csv` start with
`\xef\xbb\xbf` (UTF-8 BOM). Python's `csv.DictReader` treats
the first column name as `"\ufefftimestamp"` instead of
`"timestamp"`, so `r["timestamp"]` raises `KeyError`. Always
open with `encoding="utf-8-sig"` (BOM-tolerant).

```python
with open(DATA / "ops_audit_log.csv", encoding="utf-8-sig") as f:
    rows = list(csv.DictReader(f))
```

### 5. `step()` exception handling aborts the transaction

The first version of `step()` let exceptions propagate. A
single bad step (e.g. an FK violation) aborted the surrounding
transaction, and the `with conn.cursor()` block for subsequent
steps would fail with "current transaction is aborted, commands
ignored until end of transaction block". The fix: each step
catches, rolls back, and returns the error as a dict, so the
script finishes and the operator sees the full picture.

```python
def step(label, fn, cur, dry):
    print(f"[{label}] ...", flush=True)
    try:
        res = fn(cur, dry)
    except Exception as e:
        cur.connection.rollback()
        msg = str(e).splitlines()
        res = {"ERROR": msg[0], "DETAIL": msg[1] if len(msg) > 1 else ""}
    print(f"  {res}", flush=True)
    return res
```

### 6. Dry-run must roll back at the end

The `main()` closes with `if dry: conn.rollback() else:
conn.commit()`. Without this, a `--dry-run` followed by an
unrelated connection close could leak a partial commit. Tested.

## Usage

From the host (must have `psycopg` in the active Python env) or
from inside the web container (has `psycopg` already).

```bash
# Preview (no writes):
docker compose exec -T web python3 /app/scripts/restore_csv_data.py --dry-run

# Apply:
docker compose exec -T web python3 /app/scripts/restore_csv_data.py
```

The script connects via `DATABASE_URL` (defaults to the docker
network DSN). Exit code is 0 on success, non-zero on any
uncaught exception. Per-step `{"ERROR": ...}` is not a failure
of the script — the step rolled back and the next step ran.

## Files

- **New**: `scripts/restore_csv_data.py` (332 lines)
- **New (docs)**: this file

No other files modified.

## Verification

**Test suite**: 107/107 still pass (script is operational,
no new tests; future unit tests for `slugify` /
`voucher_id_hash` would be welcome — see open follow-ups).

**End-to-end (run on the local stack with
`PERSISTENCE_BACKEND=pg`)**:

| Step | Result |
|---|---|
| stations | 10 updated, 0 new (all 10 CSV rows already in PG from F2.5) |
| prices | 10 updated, 0 new |
| customers | 1 new (FALK), 8 updated (existing 8 refreshed) |
| audit_log | 0 new (all 48 historic rows already in PG; 48 orphans flagged and NULLed — matches F2.5 anomaly behavior) |
| vouchers (from requested) | 3 new (from 4 CSV rows; 1 dedup'd by deterministic id) |

**Idempotency**: re-run on the same DB prints `inserted: 0`
for every step. Voucher step prints `skipped: 4` (all 4 already
present by id).

**CSV/JSON pre-conditions the script assumes**:
- `data/stations.csv` header: `station_id,station_name`
- `data/customers.csv` header: `account_code,contact_name,contact_number,email,company_name,fleet_size,areas,refuel_locations,hq_locations`
- `data/ops_audit_log.csv` header: `timestamp,action,voucher_id,from_status,to_status,route,actor_ip,user_agent,note`
- `data/station_prices.json` shape: `{"stations": [{"id": ..., "price_php_per_liter": ...}, ...]}`
- `data/requested_vouchers.csv` header: `account_code,station,requested_amount_php,refuel_datetime,driver_name,vehicle_plate,truck_make,truck_model,number_of_wheels,fuel_type,contact_name,contact_number`

If any of these headers change, the script will fail loudly
(`KeyError` from `r["column_name"]`). Update the script in the
same commit.

## Known limitations

- **No JSON Schema validation** for `data/station_prices.json`.
  A malformed file produces an opaque `JSONDecodeError`. Cheap
  to add a `jsonschema` check; not done.
- **No unit tests** for `slugify`, `voucher_id_hash`, or
  `_cust_cols`. The E2E run on the local stack exercises
  these, but a focused `tests/test_restore_csv_data.py` would
  lock the contract.
- **No rollback** beyond "INSERT-only" semantics. The script
  never DELETEs from PG, so the worst case is duplicate rows
  (which the deterministic voucher_id prevents for vouchers,
  and the UPSERT prevents for the other tables). Customers
  with re-imported data: only the most recent CSV row wins per
  account_code.
- **Single-transaction model**: if one step fails, the
  preceding successful steps are rolled back. For 5-step
  restore this is fine (the whole batch is small), but a
  larger restore would benefit from per-step commits.

## Out of scope (per user direction)

- Do NOT delete `data/*.csv|json` files (CSV mode stays
  supported as a first-class option)
- Do NOT drop `CSVRepo` or `DBRepo` from `persistence.py`
- Do NOT change `PERSISTENCE_BACKEND` default from `'csv'` to
  `'pg'`
- The CSV/DB backends remain first-class options. The restore
  script is an **operator** tool, not a migration off CSV.

## Open follow-ups

- **Unit tests for `slugify`, `voucher_id_hash`, `_cust_cols`**:
  a small `tests/test_restore_csv_data.py` with 10–15 cases
  would lock the contract. Estimated: 30 min.
- **JSON Schema validation** for `data/station_prices.json`:
  cheap to add; not done.
- **Per-step commits** for larger restores (if the file count
  grows): a 1-line change in `main()`.
- **A "diff-only" mode** that prints what would change
  without doing anything (separate from `--dry-run`, which
  rolls back the transaction but still runs all the queries).
