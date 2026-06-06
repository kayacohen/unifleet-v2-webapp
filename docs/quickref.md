# Operator's quick reference

> **One-page cheatsheet. Print and pin next to the laptop.**
> **If this file ever disagrees with [`docs/runbook.md`](runbook.md), the runbook wins. Update the quickref to match.**

## If you see X, do Y

| If you see... | First action | Where to go next |
|---|---|---|
| Service is down (5xx, crash, no response) | Open Railway dashboard → `web` service → Deploys tab | [§deploy-procedure](#deploy-procedure) / [§rollback-procedure](#rollback-procedure) |
| 5xx spike right after a deploy | Check the deploy logs (last 50 lines) | [§what-healthy-looks-like](#what-healthy-looks-like) / [§on-call-runbook](#on-call-runbook) §1 |
| Deploy stuck (>10 min, no progress) | Check the build logs for the deploy stage | [§deploy-procedure](#deploy-procedure) |
| Backup missing (>25h since last run) | Check the `backup` service in the dashboard | [§backup-verification](#backup-verification) / [§on-call-runbook](#on-call-runbook) §3 |
| Volume full (>90% used) | Check `/data` and `/backups` sizes | [§on-call-runbook](#on-call-runbook) §5 |
| Need to rotate a secret | Edit the var in Railway Variables tab → redeploy → smoke test | [§secret-rotation](#secret-rotation) |
| DB restore needed (data corruption, yesterday's data) | Run `make restore-list` first, then `make restore-pg` into a test DB | [§db-restore](#db-restore) |
| Customer reports wrong/missing voucher | Check `audit_log` + `vouchers`, do NOT revert | [§on-call-runbook](#on-call-runbook) §8 |

## Top 5 env vars you'll touch most often

| Name | Where to set it | What it does |
|---|---|---|
| `DATABASE_URL` | Railway → `web` service → Variables tab | Postgres connection string (managed DB `unifleet`) |
| `PERSISTENCE_BACKEND` | Railway → `web` service → Variables tab | Selects `csv` / `db` / `pg` (must be `pg` in production) |
| `secret_key` | **HARDCODED in `main.py:102`** ⚠️ | Flask session signing key; rotation requires a source edit + redeploy, NOT a Variables-tab change |
| `ADMIN_KEY` | Railway → `web` service → Variables tab | Auth token for `/admin/*` routes |
| `SUPPLIER_API_TOKEN` | Railway → `web` service → Variables tab | Auth token for the supplier API endpoint |

## 3 commands you'll run most often

**Local dev** (against the Docker stack):

| Command | Expected output | If it doesn't match |
|---|---|---|
| `make test-db` | ends with `107 passed in <20s` | See [§local-dev-setup](#local-dev-setup) and `make help` |
| `make backup BACKUP_DIR=/tmp/unifleet-backups` | new `.pgdump` file in `BACKUP_DIR` | See [§backup-verification](#backup-verification) |
| `make restore-pg BACKUP_DIR=/tmp/unifleet-backups` | `unifleet_restore` DB created, row counts match | See [§db-restore](#db-restore) |

The Railway pass for these same flows (dashboard, smoke tests, env-var check) is documented in the runbook.
