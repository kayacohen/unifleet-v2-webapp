# ROADMAP — Post-F2 (what's next to build)

Captured after the F2.6 + CSV-restore work landed on `feature/F2.5`.
Lists the 5 open items needed before the F3 production cutover
and F4 docs/monitoring phase can begin.

## Current state (recap)

- F1.1 T1 done, T2 prep done, T2 on-Railway **blocked on `railway login`**
- F2.1 → F2.6 done; F2.4, F2.5, F2.6 + CSV-restore tool live on `feature/F2.5`
  (7 commits ahead of `main`, not yet merged)
- Local stack verified on `PERSISTENCE_BACKEND=pg` (3 Unverified vouchers
  restored, FALK customer added, 9 customers / 19 stations / 10 prices
  in PG; 107/107 unit tests pass)
- READ paths exercised end-to-end (`/form`, `/register`, `/api/v1/prices`,
  `/api/v1/discounts`, `/api/v1/price_preview`, `/supplier-sheet.pdf`,
  `/assets/qr/...`). WRITE paths (booking POST, redemption, status
  change) **not yet driven against PG**
- CSV/DB backends stay supported (per user direction); PERSISTENCE_BACKEND
  default stays `'csv'`
- `data/legacy/` exists (only `unifleet.db`); the live CSVs are still
  in `data/` (intentional, per user)
- Source of truth for next steps: `specs/plans/PROJECT-migrate-to-railway.md`
  (4-phase plan); F1+F2 done, F3 (production cutover) and F4 (docs +
  monitoring) still ahead

## Open items, prioritized

| # | Item | Why now | Effort |
|---|---|---|---|
| 1 | **End-to-end write-path test on PG** | READ paths verified; no booking POST, redemption, or status change has been driven against PG. Until we hit a write, we can't be sure `PostgresRepo` is wired correctly through `main.py`. | 30 min |
| 2 | **Merge `feature/F2.5` → `main`** | 7 commits on a side branch. Future work can't reference F2.6 / CSV-restore / F2.4 work without this. | 1 min (just a PR/merge) |
| 3 | **`scripts/restore_csv_data.py` plan doc** | Tool is committed but only documented in the commit message. Future operator picking it up cold won't know the orphan-handling rules, BOM quirks, or why the deterministic voucher id exists. Should be `specs/plans/PLAN-csv-restore.md`. | 20 min |
| 4 | **F1.1 T2 on-Railway** | Actual deployment. Unblocks F3. Still blocked on `railway login` (operator action). Once done: run `provision_railway.sh`, then `run_f1_1_verifications.sh`, verify PG + service + volume. | 1–2 hours (mostly waiting) |
| 5 | **Volume backup strategy** | `unifleet-pgdata` is the only copy of the live DB. If Railway loses the volume, all customers / vouchers / audit are gone. Need a nightly `pg_dump` to a backup Volume (or Railway's built-in Volume snapshots). | 2–3 hours (decide + implement + test restore) |

After these, the original 4-phase plan still has:

- **F3 — Production cutover** (point unifleet.asia at the new PG-backed
  web service, smoke-test on Railway, swap DNS)
- **F4 — Docs + monitoring + alerts** (README updates, PERSISTENCE_BACKEND
  selector guide, error tracking, uptime checks, on-call runbook)

## Recommended order

1. **#1 write-path test** — cheapest, highest-confidence item. Proves
   `PostgresRepo` works end-to-end. If it fails, we know now, not
   during F3.
2. **#2 merge to main** — unblocks future work to be on `main`.
3. **#3 plan doc for the restore tool** — quick write-up, captures
   decisions that are currently only in the commit message.
4. **#4 F1.1 T2 on-Railway** — the actual deployment; unblocks F3.
   Requires `railway login` (operator action).
5. **#5 volume backup** — must be in place before F3 cutover. Losing
   the only DB copy post-cutover would be catastrophic.

## Notes

- All items except #4 are local-only and can be done now.
- #4 and #5 gate F3. F3 gates the unifleet.asia DNS swap.
- No work is in scope that touches the F2.6/CSV-mode "keep both
  backends supported" decision.
