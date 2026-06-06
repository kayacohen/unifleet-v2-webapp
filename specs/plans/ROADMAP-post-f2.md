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

- ✅ **#3 Plan doc for `scripts/restore_csv_data.py`** (commits
  `ac7c15f` + `149e3c5`)
  - New `specs/plans/PLAN-csv-restore.md` (293 lines) captures:
    the 5 source/destination pairs, the deterministic
    `voucher_id` rule, the orphan-voucher_id handling for
    audit_log, the 6 bugs hit during dev (legacy_id type,
    `::text` cast, slugify internal whitespace, CSV BOMs,
    `step()` rollback, dry-run transaction close), the header
    pre-conditions for each source, and 4 follow-up items
  - Also fixed a side issue: `data/legacy/unifleet.db` was
    accidentally committed in the same push; `git rm --cached`
    + `.gitignore` entry for `data/legacy/*.db` resolves it
    (file stays on disk for rollback)

- ✅ **#5 Volume backup strategy** (commits `e05b82c` + `068a04d`)
  - New `specs/plans/PLAN-pg-backup.md` (249 lines): the
    threat model, the `pg_dump --format=custom` choice, the
    Railway Cron Schedule service design, the 6-step restore
    procedure, RTO<30min / RPO<24h targets, the optional S3
    off-platform copy, the 4 things explicitly out of scope
    (PITR, encryption config, verification cron, alerting)
  - New `scripts/backup_postgres.py` (~180 lines): orchestrates
    pg_dump, rotation, optional S3 upload. Stdlib only
    (boto3 is optional; skipped gracefully if missing).
    Handles DSN redaction in logs, `--dry-run`, and cleans
    up the 0-byte placeholder pg_dump leaves on failure
  - New `Dockerfile.backup` (~30 lines): production image
    (postgres:16-alpine + python3 + boto3 + script as
    ENTRYPOINT). 359 MB. Used by Railway Cron Schedule
    service in F1.1 T2 (#4)
  - Modified `Makefile` (+49 lines): 4 new targets —
    `make backup`, `make restore-list`, `make restore-pg`,
    `make backup-clean`. All accept `BACKUP_DIR=...`
    override (data/legacy/ is root-owned in this dev env)
  - Smoke-tested end-to-end against local `unifleet-db`:
    23 KB pg_dump (55 TOC entries, gzip-compressed),
    `pg_restore` round-trip into `unifleet_restore` DB
    passes row-count check (19/10/9/3/49 = source),
    rotation deletes 3 files mtime'd to 30 days ago,
    bad DSN leaves no 0-byte file, missing pg_dump gives
    clear error, idempotent re-run, 107/107 unit tests
    still pass

- ✅ **F4 plan drafted** (`specs/plans/PLAN-railway-ops-runbook.md`,
  341 lines, NEW in working tree, NOT YET COMMITTED):
  - Two new docs in the repo: `docs/quickref.md` (~80 lines,
    table-only cheatsheet) + `docs/runbook.md` (~500 lines,
    12 structured sections covering Topology / Dashboard
    bookmarks / Deploy procedure / What "healthy" looks like /
    Monitoring / Rollback / DB restore / Secret rotation /
    Local dev setup / On-call runbook / Backup verification /
    Env var reference)
  - Tailored for the operator's manual-dashboard-deploy
    workflow (no CLI, no special branch, no CI this round)
  - Includes 12 explicit decisions, 10 edge cases + failure
    modes, and 3 open questions captured as follow-ups
    (Sentry/error tracking, Slack/email alerts, CI/CD)
  - Plus 1-line README link addition (not a README rewrite)
  - Plus 2 verification tasks: walk through every command in
    the runbook against the local Docker stack; walk through
    the env-var reference against `main.py`
  - Tasks not yet generated — next step is `generate-tasks`
    from the plan, then `tdd` to implement

## Open items, prioritized

| # | Item | Why now | Effort |
|---|---|---|---|
| ~~1~~ | ~~End-to-end write-path test on PG~~ | Done — see Completed above. | — |
| ~~2~~ | ~~Merge `feature/F2.5` → `main`~~ | Done — see Completed above. | — |
| ~~3~~ | ~~`scripts/restore_csv_data.py` plan doc~~ | Done — see Completed above. | — |
| ~~5~~ | ~~Volume backup strategy~~ | Done — see Completed above. | — |
| 4 | **F1.1 T2 on-Railway** | The actual deployment. Unblocks everything else. Still blocked on `railway login` (operator action). Once done: run `provision_railway.sh`, then `run_f1_1_verifications.sh`, verify PG + service + volume. Also: provision the `backup` service from the new `Dockerfile.backup` + a 2nd `unifleet-pgdata-backups` volume. | 1–2 hours (mostly waiting) |

## F3 (cutover) — operator-owned, terminated as a planned deliverable

The user has explicitly terminated the F3 cutover as a planned
deliverable from this session. The cutover (Replit → Railway, DNS
swap, smoke test, Replit decommission) will be performed manually
by the operator. No plan artifact for F3. The work is tracked only
in the user's head + the ANCHOR's "operator-owned" entry.

Original F3 features (F3.1-F3.7 in `PROJECT-migrate-to-railway.md`)
were ALSO terminated — production-readiness hardening (mandatory
env-driven secrets, CSRF, structured logging, phase ordering,
dedupe helpers, /discount-locator resolution) is **permanently
skipped** per the user's decision. The Railway ops runbook
acknowledges this in a "Why this runbook doesn't have X" section.

## F4 (docs + monitoring) — plan done, tasks not yet generated

- ✅ **`specs/plans/PLAN-railway-ops-runbook.md`** (341 lines, NEW
  in working tree, NOT YET COMMITTED): operator runbook + quickref
  for the manual Railway deployment. Two new docs in the repo:
  - `docs/quickref.md` (~80 lines, table-only cheatsheet)
  - `docs/runbook.md` (~500 lines, 12 structured sections: Topology,
    Dashboard bookmarks, Deploy procedure, What "healthy" looks
    like, Monitoring, Rollback, DB restore, Secret rotation, Local
    dev setup, On-call runbook, Backup verification, Env var
    reference)
  - Plus a 1-line README link addition (not a README rewrite)
  - Plus 2 verification tasks: walk through every command in
    the runbook against the local Docker stack; walk through
    the env-var reference against `main.py`
- **Next step**: review the plan; then `generate-tasks` from it
  to produce the TDD-ready task specs; then `tdd` to implement.

## Recommended order

1. **Review + commit `PLAN-railway-ops-runbook.md`** (so it's
   pushed and survives the session boundary).
2. **#4 F1.1 T2 on-Railway** — the actual deployment; unblocks
   F3. Requires `railway login` (operator action). Includes
   provisioning the `backup` Cron Schedule service with the new
   `Dockerfile.backup` + a `unifleet-pgdata-backups` Volume +
   `DATABASE_URL` env var.
3. **F3 (cutover) — operator-owned**: the operator runs the
   cutover using the runbook as their playbook. No automated
   work, no PRs from this session.
4. **F4 (docs + monitoring) — task generation + TDD**: review
   the plan, generate tasks from it, implement the docs,
   verify, commit. ~6 tasks, ~1-2 hours.
5. **Future follow-ups** (open questions in the plan): Sentry
   or similar for error tracking; Slack/email alerts; CI/CD
   pipeline; geo-redundancy for backups; per-env (staging/prod)
   distinction.

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
