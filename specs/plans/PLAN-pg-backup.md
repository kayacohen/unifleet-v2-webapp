# PLAN — Postgres backup for `unifleet-pgdata`

> Nightly logical backups (`pg_dump --format=custom`) of the
> live Postgres database to a 2nd Railway Volume, with optional
> S3 off-platform copy. Idempotent, restartable, retains last
> 14 days on the volume + 90 days off-platform (if S3 is
> configured). Local dev gets the same script run via docker
> compose.

**Feature spec:** `specs/plans/ROADMAP-post-f2.md` item #5
**Depends on:** F2.1-F2.5 (PG schema + repo + migration are stable)
**Status:** design only (plan doc); implementation in next commit
**Risk if not done:** if `unifleet-pgdata` is lost (Railway
volume failure, account compromise, accidental `DROP DATABASE`),
all customers / vouchers / audit / price history vanish
irrecoverably. Production cutover (F3) is unsafe without this.

## Why this matters

Railway Volumes are persistent, but they're not backed up by
default. Three failure modes are unaccounted for today:

1. **Railway volume failure** — disk corruption or Volume
   detach. Recovery: rebuild from a recent dump.
2. **Account compromise** — attacker runs `DROP DATABASE` or
   `DELETE FROM customers`. Recovery: restore from a clean
   dump.
3. **Bad migration** — a future DDL change corrupts data and
   the corruption isn't noticed for a few hours. Recovery:
   point-in-time restore from yesterday's dump.

Without backups, any of these is a full-data-loss event.

## Approach

### Production (Railway)

A separate **`backup` service** in the same Railway project,
configured as a Cron Schedule service:

| Setting | Value |
|---|---|
| Image | `postgres:16-alpine` (has `pg_dump` built in) |
| Schedule | `0 3 * * *` (3 AM UTC = 11 AM Singapore) |
| Command | `/bin/sh -c "pg_dump --format=custom --no-owner --no-privileges --file=/backups/$$(date +%Y%m%d-%H%M%S).pgdump $$DATABASE_URL && (s3-upload step)"` |
| Env | `DATABASE_URL` (same as web), optional `AWS_*` for S3 |
| Volume | `unifleet-pgdata-backups` mounted at `/backups` |
| Retention | Volume: 14 days (script-driven); S3: 90 days (lifecycle policy) |

Railway's Cron Schedule runs the service on the cron
expression, then sleeps the container until the next run.
Cold start is fast (~2 s) and the volume outlives the
container, so backups accumulate cleanly.

### Local dev

The same `pg_dump` is run **from the existing `db` container**
(not a new service) via a `make backup` target. Output is
written to a host bind mount at `./data/legacy/backups/` so
operators can inspect, copy out, or version them. No new
service in the local compose file; the existing `db` container
already has `pg_dump` and direct access to the data dir.

This intentionally **deviates** from the production setup
(no cron, no second volume) because the local stack is for
development, not for proving the production path. The
production setup is proven in F1.1 T2 on-Railway execution
(#4 in the roadmap, blocked on `railway login`).

## Backup strategy choice: `pg_dump --format=custom`

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| `pg_dump --format=custom` (logical) | Single file, `pg_restore` is one command, easy to test locally, ~3x smaller than SQL, supports parallel restore | Slower than physical for large DBs, no PITR | ✅ **chosen** |
| `pg_dump --format=plain` (SQL) | Human-readable | 3-5x larger, no parallel restore, slower | ❌ |
| `pg_basebackup` (physical) | Fast, supports PITR with WAL archive | Complex restore (need matching PG version, initdb steps), WAL archive needed for PITR | ❌ (overkill) |
| Logical replication | Hot standby, near-zero RPO | Operates a 2nd DB to maintain, more moving parts | ❌ (overkill) |

DB size at present: 8 tables, ~90 rows total, `pg_dump --format=custom` produces a ~10 KB file. Even at 100x growth (9,000 rows) the dump is ~1 MB. Compression is unnecessary.

## Script

`scripts/backup_postgres.py` (~100 lines, stdlib only):

- Reads `DATABASE_URL` (required) and `UNIFLEET_BACKUP_DIR` (default `/backups`)
- Calls `pg_dump --format=custom --no-owner --no-privileges --file=$DEST $DATABASE_URL`
- On success: optionally uploads to S3 (if `UNIFLEET_BACKUP_S3_BUCKET` is set)
- On success: rotates backups older than `UNIFLEET_BACKUP_RETAIN_DAYS` (default 14)
- On failure: logs to stderr with full exit code + stderr from `pg_dump`, exits non-zero
- All output (success/failure) appended to `backup.log` in the backup dir for debugging

Key design choices:
- **Subprocess over psycopg**: `pg_dump` is the official tool, handles edge cases (large objects, ACLs) that a Python-side serializer wouldn't. Trade 100ms for correctness.
- **`--no-owner --no-privileges`**: makes the dump portable across environments (the local dev DB and Railway dev DB have different roles).
- **Single-process**: no concurrency; the cron fires at 3 AM, takes <1s, done. No need for a queue.
- **Idempotent on retry**: filename includes `YYYYMMDD-HHMM%S`, so two runs in the same minute produce different files. Re-running after a failure is safe.

## Restore procedure (documented, not automated)

```bash
# 1. List available backups
ls -lh data/legacy/backups/         # local dev
# or, on Railway:
docker exec <backup-container> ls -lh /backups/

# 2. Copy the chosen backup out
cp data/legacy/backups/unifleet-20260606-030000.pgdump /tmp/restore.pgdump

# 3. Create a fresh DB (don't overwrite the live one)
createdb -h db -U unifleet unifleet_restore

# 4. Restore
pg_restore -h db -U unifleet -d unifleet_restore \
  --no-owner --no-privileges --clean --if-exists \
  /tmp/restore.pgdump

# 5. Verify (compare row counts)
psql -h db -U unifleet -d unifleet_restore -c "
  SELECT 'stations' AS t, COUNT(*) FROM stations
  UNION ALL SELECT 'customers', COUNT(*) FROM customers
  UNION ALL SELECT 'vouchers', COUNT(*) FROM vouchers
  UNION ALL SELECT 'audit_log', COUNT(*) FROM audit_log;"

# 6. If correct, swap (manual):
#    - stop web service
#    - drop live DB, rename restore DB to live
#    - restart web service
```

RTO (recovery time objective) target: **<30 minutes** for a
full restore to a fresh DB. RPO (recovery point objective)
target: **<24 hours** (one missed backup = at most 24h of data
loss; the cron is nightly).

## Off-platform copy (S3, optional but recommended)

If `UNIFLEET_BACKUP_S3_BUCKET` is set in the backup service's
env, after each successful local dump the script also uploads
to S3:

```python
if os.environ.get("UNIFLEET_BACKUP_S3_BUCKET"):
    boto3.client("s3").upload_file(
        str(DUMP_FILE),
        os.environ["UNIFLEET_BACKUP_S3_BUCKET"],
        f"unifleet/{DUMP_FILE.name}",
        ExtraArgs={"StorageClass": "STANDARD_IA"},  # ~$0.0125/GB/mo
    )
```

S3 lifecycle policy (set on the bucket, not in code):

| Age | Storage class | Cost |
|---|---|---|
| 0-30 days | STANDARD_IA | $0.0125/GB/mo |
| 30-90 days | GLACIER_IR | $0.004/GB/mo |
| 90+ days | EXPIRED (delete) | — |

Total cost: a 1 MB backup × 90 days × STANDARD_IA = $0.001/year.
Effectively free. The off-platform copy is the real insurance
against Railway-level failures (account compromise, region
outage).

B2 / R2 / GCS work the same way with `boto3` + a different
endpoint URL. The script is provider-agnostic.

## Local dev additions

`Makefile` gets a new target:

```make
backup: ## Run a Postgres backup to ./data/legacy/backups/
	$(COMPOSE) exec -T db sh -c \
	  "pg_dump --format=custom --no-owner --no-privileges \
	   --file=/backups/unifleet-\$$(date +%Y%m%d-%H%M%S).pgdump \
	   $$DATABASE_URL"
	@echo "Backups in ./data/legacy/backups/:"
	@ls -lh data/legacy/backups/*.pgdump 2>/dev/null || echo "(none)"
```

Wait, that won't work because the db container doesn't have
`/backups` mounted. The local dev backup is more like:

```make
backup: ## Run a Postgres backup, write to host ./data/legacy/backups/
	mkdir -p data/legacy/backups
	docker compose exec -T db pg_dump -U unifleet -d unifleet \
	  --format=custom --no-owner --no-privileges \
	  > data/legacy/backups/unifleet-$$(date +%Y%m%d-%H%M%S).pgdump
	@ls -lh data/legacy/backups/*.pgdump 2>/dev/null | tail -3
```

(uses stdout streaming instead of `--file` inside the container; the
pg_dump binary writes to the host via the compose `exec` pipe.)

## What's NOT in this plan

- **PITR (point-in-time recovery)**: requires WAL archiving
  + `restore_command` setup. Overkill for 90 rows and a
  1-night RPO. Add later if data volume grows.
- **Encryption at rest**: Railway Volumes are encrypted at
  the storage layer by default; S3 buckets need a bucket
  policy (set in the cloud console, not in code).
- **Backup verification cron**: a "restore to a fresh DB and
  count rows" cron would catch silent corruption. Not done
  in v1; can be added as a 2nd cron entry that runs hourly.
- **Monitoring/alerting on backup failure**: would need a
  3rd service (or a webhook from cron to Slack/email). Not
  in scope; flagged as a F4 (docs + monitoring) item.

## Open follow-ups (after implementation)

- Unit tests for `scripts/backup_postgres.py` (rotation logic, env parsing)
- Backup verification cron (hourly restore-and-count)
- Alerting on backup failure (webhook to Slack or email)
- Move backups off-Railway to B2/R2 (cheaper, separate blast radius)
- Document restore procedure in `docs/runbook.md` (F4)

## Files

**New**:
- `specs/plans/PLAN-pg-backup.md` (this file)
- `scripts/backup_postgres.py` (~100 lines)

**Modified**:
- `Makefile` — add `backup` target
- (Optional) `data/legacy/backups/.gitkeep` — placeholder so the dir is in the repo's view

**Railway** (operator action in #4):
- New `backup` service in the project
- New `unifleet-pgdata-backups` Volume
- New `DATABASE_URL` + optional `AWS_*` env vars on the backup service
- Cron Schedule `0 3 * * *` configured in Railway UI

## Verification (post-implementation)

- Local: `make backup` creates a `*.pgdump` in `data/legacy/backups/`; `pg_restore` round-trips cleanly
- Local: row counts in the restored DB match the live DB
- Production: not tested until #4 (F1.1 T2 on-Railway) runs; the script is runnable on Railway (uses `postgres:16-alpine` image which has `pg_dump`)
- S3 upload: not tested until AWS credentials are configured (not in scope for this plan)

## Out of scope (per user direction, reinforced)

- Do NOT delete `data/*.csv|json` files
- Do NOT drop `CSVRepo` or `DBRepo` from `persistence.py`
- Do NOT change `PERSISTENCE_BACKEND` default from `'csv'`
- The CSV/DB backends remain first-class options
- The backup tool covers the PG backend only (CSV/DB backends
  are not used in production; only kept for dev/testing)
