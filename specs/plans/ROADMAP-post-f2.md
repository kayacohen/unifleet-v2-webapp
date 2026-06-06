# ROADMAP — Post-F2 (what's next to build)

Captured after the F2.6 + CSV-restore work landed on `feature/F2.5`.
Lists the open items needed before the F3 production cutover
and F4 docs/monitoring phase can begin.

## Current state (recap)

- F1.1 T1 done, T2 prep done, T2 on-Railway **blocked on `railway login`**
- F2.1 → F2.6 done; F2.4, F2.5, F2.6 + CSV-restore tool + write-path
  fixes live on `feature/F2.5` (8 commits ahead of `main`, not yet
  merged)
- Local stack verified on `PERSISTENCE_BACKEND=pg` (3 Unverified vouchers
  restored, FALK customer added, 9 customers / 19 stations / 10 prices
  in PG; 107/107 unit tests pass)
- READ paths exercised end-to-end (`/form`, `/register`, `/api/v1/prices`,
  `/api/v1/discounts`, `/api/v1/price_preview`, `/supplier-sheet.pdf`,
  `/assets/qr/...`)
- **WRITE paths verified end-to-end** (#1 below — see Completed section)
- CSV/DB backends stay supported (per user direction); PERSISTENCE_BACKEND
  default stays `'csv'`
- `data/legacy/` exists (only `unifleet.db`); the live CSVs are still
  in `data/` (intentional, per user)
- Source of truth for next steps: `specs/plans/PROJECT-migrate-to-railway.md`
  (4-phase plan); F1+F2 done, F3 (production cutover) and F4 (docs +
  monitoring) still ahead

## Completed

- ✅ **#1 End-to-end write-path test on PG** (commit `f2a3e8c`)
  - Drove the full voucher lifecycle against PG: `POST /book` →
    `GET /ops/.../Unredeemed` → `POST /redeem/<id>` → `GET
    /supplier-api/<id>?token=...`
  - Result: voucher created with status=Unverified, promoted to
    Unredeemed (QR + branded PNG generated, liters_requested
    computed = 16.67), redeemed (redemption_timestamp set),
    supplier API returned full JSON with all PG-loaded values
  - **Surfaced 2 real bugs** that were latent in CSV/SQLite mode;
    both fixed in the same commit:
    1. `PostgresRepo.append_vouchers` was passing epoch-int values
       to TIMESTAMPTZ columns (`price_snapshot_updated_at`,
       `discount_snapshot_captured_at`). New `_to_timestamptz()`
       helper normalizes epoch-int / ISO-string / datetime / None
       into a tz-aware UTC datetime before INSERT.
    2. `generate_voucher.generate_branded_image` was passing raw
       row values to Pillow's `draw.text`, which expects an
       iterable (str). After fix #1, `expected_refill_date` is a
       datetime object → TypeError. New nested `_fmt()` helper
       renders datetime as YYYY-MM-DD, None as '', everything else
       via str().
  - Test data cleaned up: test voucher + 3 audit_log rows + 2
    asset files + 1 HARR_presets.csv all removed. PG back to
    3 vouchers / 49 audit_log rows.
  - 107/107 unit tests still pass after the fix.

- ✅ **#2 Merge `feature/F2.5` → `main`** (fast-forward, 9 commits)
  - `main` advanced from `c45a461` (F2.3 T1) → `66c2cc7`
    (write-path fixes / roadmap)
  - 19 files changed, +2077 / -67 lines
  - New files on main: `data_paths.py`, `audit_log.py`,
    `scripts/migrate_to_postgres.py`, `scripts/restore_csv_data.py`,
    `tests/test_data_paths.py`, all 4 `specs/plans/PLAN-*.md` +
    `ROADMAP-post-f2.md`
  - Pushed to origin: `c45a461..66c2cc7  main -> main`

## Open items, prioritized

| # | Item | Why now | Effort |
|---|---|---|---|
| ~~1~~ | ~~End-to-end write-path test on PG~~ | Done — see Completed above. | — |
| ~~2~~ | ~~Merge `feature/F2.5` → `main`~~ | Done — see Completed above. | — |
| 3 | **`scripts/restore_csv_data.py` plan doc** | Tool is committed but only documented in the commit message. Future operator picking it up cold won't know the orphan-handling rules, BOM quirks, or why the deterministic voucher id exists. Should be `specs/plans/PLAN-csv-restore.md`. | 20 min |
| 4 | **F1.1 T2 on-Railway** | Actual deployment. Unblocks F3. Still blocked on `railway login` (operator action). Once done: run `provision_railway.sh`, then `run_f1_1_verifications.sh`, verify PG + service + volume. | 1–2 hours (mostly waiting) |
| 5 | **Volume backup strategy** | `unifleet-pgdata` is the only copy of the live DB. If Railway loses the volume, all customers / vouchers / audit are gone. Need a nightly `pg_dump` to a backup Volume (or Railway's built-in Volume snapshots). | 2–3 hours (decide + implement + test restore) |

After these, the original 4-phase plan still has:

- **F3 — Production cutover** (point unifleet.asia at the new PG-backed
  web service, smoke-test on Railway, swap DNS)
- **F4 — Docs + monitoring + alerts** (README updates, PERSISTENCE_BACKEND
  selector guide, error tracking, uptime checks, on-call runbook)

## Recommended order

1. **#3 plan doc for the restore tool** — quick write-up, captures
   decisions that are currently only in the commit message.
2. **#4 F1.1 T2 on-Railway** — the actual deployment; unblocks F3.
   Requires `railway login` (operator action).
3. **#5 volume backup** — must be in place before F3 cutover. Losing
   the only DB copy post-cutover would be catastrophic.

## Side observations (not yet scoped)

- **docker-compose.yml doesn't bind-mount `db/` or root-level
  `.py` modules** (only `./data`, `./static`, `./templates`). Code
  changes to `db/postgres_repo.py` or `generate_voucher.py` need
  `docker compose restart web` (and `docker cp` for the new code
  to land in the container). The F2.2 test workflow uses
  `docker-compose.test.yml` with full bind mount; the dev compose
  could mirror that. Low priority — production deploy will use the
  image, not bind mounts.
- **No unit tests for `_to_timestamptz`** (the new helper from
  commit `f2a3e8c`). The end-to-end write-path test exercises it,
  but a focused `tests/test_timestamptz_normalization.py` would
  lock the contract (epoch-int / ISO / datetime / None paths).
  Low priority — the helper is small and well-documented.

## Notes

- All items except #4 are local-only and can be done now.
- #4 and #5 gate F3. F3 gates the unifleet.asia DNS swap.
- No work is in scope that touches the F2.6/CSV-mode "keep both
  backends supported" decision.
