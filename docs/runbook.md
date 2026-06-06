# Railway operations runbook

> **Canonical operator manual for the UniFleet v2 webapp on Railway.**
> **This is the source of truth. If [`docs/quickref.md`](quickref.md) ever disagrees with this file, the runbook wins.**

## Contents

1. [Topology](#topology)
2. [Dashboard bookmarks](#dashboard-bookmarks)
3. [Deploy procedure](#deploy-procedure)
4. [What "healthy" looks like](#what-healthy-looks-like)
5. [Monitoring](#monitoring)
6. [Rollback procedure](#rollback-procedure)
7. [DB restore](#db-restore)
8. [Secret rotation](#secret-rotation)
9. [Local dev setup](#local-dev-setup)
10. [On-call runbook](#on-call-runbook)
11. [Backup verification](#backup-verification)
12. [Env var reference](#env-var-reference)
13. [Why this runbook doesn't have X](#why-this-runbook-doesnt-have-x)

---

## 1. Topology

The UniFleet v2 deployment on Railway consists of:

```
                        unifleet.asia (DNS)
                                │
                                ▼
                  ┌──────────────────────────────┐
                  │   Railway service: web       │
                  │   (Dockerfile, gunicorn)     │
                  │                              │
                  │  Volume: data    /data       │  ← generated QR codes,
                  │                              │    branded PNGs, presets
                  └──────────────────────────────┘
                                │
                  ┌─────────────┴─────────────┐
                  ▼                           ▼
   ┌─────────────────────────┐   ┌────────────────────────────┐
   │  Railway Postgres 16    │   │  Railway service: backup   │
   │  Database: unifleet     │   │  (Dockerfile.backup, cron)  │
   │                         │   │                            │
   │  Volume: pgdata         │   │  Volume: unifleet-pgdata-  │
   │                         │   │  backups  /backups         │
   └─────────────────────────┘   └────────────────────────────┘
```

| Resource | Name | Type | Notes |
|---|---|---|---|
| Project | `unifleet` | Railway project | Hobby plan, region `asia-southeast` |
| Service | `web` | Web service | Main Flask app, gunicorn, Dockerfile |
| Service | `backup` | Cron Schedule service | `Dockerfile.backup`, runs `0 3 * * *` (3 AM UTC = 11 AM SGT) |
| Database | `unifleet` | Managed Postgres 16 | Connection via `DATABASE_URL` env var |
| Volume | `data` | Railway Volume | Mounted at `/data` on `web` |
| Volume | `unifleet-pgdata-backups` | Railway Volume | Mounted at `/backups` on `backup` |
| Domain | `unifleet.asia` | DNS A/CNAME | Points at Railway's edge IP |

## 2. Dashboard bookmarks

Bookmark these on day 1. The exact URLs depend on the project ID assigned by Railway; replace `<project-id>` and `<service-id>` with the actual values shown in the dashboard URL bar.

| Resource | Path (replace placeholders) |
|---|---|
| Project root | `https://railway.app/project/<project-id>` |
| `web` service | `https://railway.app/project/<project-id>/service/<web-service-id>` |
| `web` → Deploys tab | `<web-service-url> → Deploys` |
| `web` → Variables tab | `<web-service-url> → Variables` |
| `web` → Metrics tab | `<web-service-url> → Metrics` |
| `backup` service | `https://railway.app/project/<project-id>/service/<backup-service-id>` |
| `backup` → Last run | `<backup-service-url> → Settings → Cron Schedule` |
| `unifleet` database | `https://railway.app/project/<project-id>/database/<db-id>` |
| `unifleet` → Metrics | `<db-url> → Metrics` |
| Public app (pre-cutover) | `https://<web-service>.up.railway.app` |
| Public app (post-cutover) | `https://unifleet.asia` |

## 3. Deploy procedure

The deploy is triggered manually from the Railway dashboard. No CLI. No CI. No special branch.

**Steps:**

1. **Push to `main` on GitHub.** Local discipline: run `make test-db` first. The push triggers Railway's build hook.
2. **Open the Railway dashboard** → `web` service → Deploys tab. A new deploy appears at the top of the list with status "Building".
3. **Watch the deploy logs.** Click the deploy row to expand the build + runtime logs. The build takes 1-3 minutes (Poetry install + Docker build). Look for "Build successful" → "Deploying" → "Running".
4. **Wait for "Active" status.** The deploy row turns green. The previous deploy is still serving traffic during the switchover (zero-downtime deploy via gunicorn's graceful restart).
5. **Smoke test.** See [§what-healthy-looks-like](#what-healthy-looks-like) for the route list. The minimum smoke test is `curl -I https://<web-service>.up.railway.app/healthz` returning 200.

**Is it healthy? Decision rule:**

| Symptom | Likely cause | Action |
|---|---|---|
| Deploy fails at `poetry install` | Missing dep in `pyproject.toml` | Fix `pyproject.toml`, commit, redeploy |
| Deploy fails at Docker build | Missing system package (e.g. `libfreetype`) | Add to `Dockerfile` apt-get list, commit, redeploy |
| Deploy succeeds but gunicorn crashes | Crash loop in app startup (often a missing env var) | See [§on-call-runbook §6](#on-call-runbook) (deploy stuck) |
| Deploy succeeds, smoke test fails | Code bug or data-shape change | See [§rollback-procedure](#rollback-procedure) |
| Deploy succeeds, smoke test passes | You're done | Stop watching |

**What if it's not healthy:** do NOT mark the deploy as successful in your head. Revert via [§rollback-procedure](#rollback-procedure), then investigate.

## 4. What "healthy" looks like

After every deploy, run the smoke test list. The first route (`/healthz`) is the minimum; the rest are "is the actual app working?".

| Route | Method | Expected response | What it checks |
|---|---|---|---|
| `/healthz` | GET | 200, body `OK` | App is up, gunicorn workers responding |
| `/form` | GET | 200, HTML | Public booking form renders (uses `data_paths` + `price_store`) |
| `/api/ops/vouchers.json` | GET | 200, JSON array | PG read path works; vouchers come back from `PostgresRepo.list_recent` |
| `/api/v1/prices` | GET | 200, JSON | `price_store` (PG-backed) returns all 19 stations |
| `/api/v1/discounts` | GET | 200, JSON | `discount_store` (PG-backed) returns all 10 station discounts |
| `/admin/prices` | GET | 302 (redirect to login) or 200 if logged in | Admin route is reachable, auth gate works |
| `/assets/qr/<vid>_Official.png` | GET | 200, Content-Type `image/png` | Volume `data` is mounted at `/data`; QR files are served from `data_paths.QR_DIR` |
| `/supplier-sheet.pdf?token=...` | GET | 200, Content-Type `application/pdf` | reportlab in-memory PDF generation works |

**Smoke test command (one-liner, run from a shell with `curl`):**

```bash
URL=https://<web-service>.up.railway.app  # or https://unifleet.asia post-cutover
for route in /healthz /form /api/ops/vouchers.json /api/v1/prices; do
  status=$(curl -s -o /dev/null -w "%{http_code}" "$URL$route")
  echo "$status $route"
done
```

**Expected output:** `200 /healthz`, `200 /form`, `200 /api/ops/vouchers.json`, `200 /api/v1/prices`. If any of these is `5xx`, see [§on-call-runbook](#on-call-runbook) §1 or §2.

## 5. Monitoring

The Railway dashboard exposes the metrics you need. There is no third-party APM this round.

**Web service (`web`):**

| Panel | What's normal | Warning sign |
|---|---|---|
| CPU% | < 30% sustained | > 70% sustained for >5 min |
| Memory | < 200 MB (typical for gunicorn + Flask) | > 400 MB (memory leak?) |
| Request count | depends on traffic | sudden drop = customers can't reach the service |
| Response time (p50) | < 200 ms | > 1 s sustained |
| Error rate (5xx) | 0 | > 0.5% sustained for >5 min |

**Postgres database (`unifleet`):**

| Panel | What's normal | Warning sign |
|---|---|---|
| Storage used | grows slowly (a few MB/month) | > 80% of volume size (Hobby plan = 5 GB default) |
| Connections | < 20 typical (Hobby plan limit: 100) | > 80 sustained = pool exhaustion risk |
| CPU% | < 10% typical | > 50% sustained = slow-query problem |

**Volumes:**

| Volume | What's normal | Warning sign |
|---|---|---|
| `data` (on `web`) | grows by ~10 MB per voucher (QR + branded PNG + PDF) | > 80% of volume size |
| `unifleet-pgdata-backups` (on `backup`) | grows by ~25 KB per backup × 14 days retention = ~350 KB | > 80% of volume size |

**How often to check:**

- **Right after a deploy:** all panels, especially error rate and response time.
- **First week after cutover:** 2-3x per day, especially the volume sizes and backup last-run timestamp.
- **Steady state:** once a day is enough. The [§on-call-runbook](#on-call-runbook) decision tree is your guide for "if a panel looks weird."

## 6. Rollback procedure

Rollback is for "the new code is broken, the old code was fine." For data corruption, use [§db-restore](#db-restore) instead — rolling back the code won't fix the data.

**Steps:**

1. **Open Railway dashboard** → `web` service → Deploys tab.
2. **Find the last known-good deploy.** Railway keeps the last 5-10 successful deploys in the list. Look for the one with the green dot AND a recent smoke-test-passed comment (if you've been using the optional "comment" field).
3. **Click "..." on that deploy row** → "Redeploy". Confirm.
4. **Watch the build + deploy logs.** Same as a normal deploy. Takes 1-3 minutes.
5. **Smoke test.** Run the route list from [§what-healthy-looks-like](#what-healthy-looks-like). If green: stop. If still red: the problem is data, not code; go to [§db-restore](#db-restore).
6. **Commit the revert (optional but recommended).** Redeploying in the dashboard does NOT create a git commit. If you want the rollback to be permanent (so a future push doesn't re-introduce the bad code), `git revert` the bad commit locally, push, and the next deploy will pick it up cleanly.

**Decision rule (when to rollback vs. when to investigate):**

| Symptom | Action |
|---|---|
| Smoke test failed right after deploy | Rollback. The deploy is the cause. |
| Smoke test passed, customer reported a bug 2 hours later | Investigate first. Rollback is a last resort. The bug is data, not code. |
| 5xx spike, no recent deploy | Look at the access logs. Not a rollback candidate. |
| 5xx spike, recent deploy within the last hour | Rollback. Strong correlation. |

**The "previous" of last-resort fallback:** if all the deploys in the dashboard history are bad, the last known-good commit is whatever is in the git log before the bad code was pushed. Revert locally to that SHA, push, and the redeploy will pull it.

## 7. DB restore

This section reproduces the 6-step procedure inline. Operators should be able to restore the DB without opening `PLAN-pg-backup.md` during an incident.

**RTO target:** < 30 minutes. **RPO target:** < 24 hours (nightly cron).

**When to use this section:**

- The data is corrupted (bad migration, accidental write, etc.)
- You need yesterday's data, not last week's
- A rollback didn't fix the issue because the issue is data, not code

**When NOT to use this section:**

- The code is broken but the data is fine → [§rollback-procedure](#rollback-procedure) is faster
- You need to test a migration → use the test DB (`unifleet_test`), not production

### 6-step restore procedure

1. **Identify the backup to restore from.** Run `make restore-list` (or `pg_restore --list <backup-file>`) to see the TOC. The file with the most recent timestamp is your default target, but check the file size — a 0-byte file means the backup failed and is unsafe. Pick the most recent non-empty backup.

2. **Verify the backup is good BEFORE touching the live DB.** Run `pg_restore --list <backup-file>` and confirm you see table names you recognize (`vouchers`, `stations`, `customers`, `audit_log`, `prices`, `discounts`, etc.). If the TOC is empty or looks wrong, stop — the backup is corrupted.

3. **Create a fresh target DB.** Do NOT restore into the live `unifleet` DB yet. Use a name like `unifleet_restore`:
   ```bash
   createdb unifleet_restore
   ```

4. **Restore into the target DB:**
   ```bash
   pg_restore --no-owner --no-privileges --dbname=unifleet_restore <backup-file>
   ```
   Expect some warnings about "errors ignored during restore" (CREATE EXTENSION, etc.). These are normal. The restore exits 0 if the data is good.

5. **Verify row counts match the source.** Run these on BOTH the source DB and the restored DB, compare:
   ```bash
   for table in stations customers prices discounts audit_log vouchers; do
     echo "$table:"
     psql unifleet         -c "SELECT count(*) FROM $table"
     psql unifleet_restore -c "SELECT count(*) FROM $table"
   done
   ```
   Expected row counts (approximate, depends on current data):
   - `stations`: 19
   - `customers`: 9
   - `prices`: 10
   - `discounts`: 10
   - `audit_log`: ~50
   - `vouchers`: 3-10 (varies; small dataset)

   If row counts differ by more than a few %, do NOT promote — investigate.

6. **Promote to live (only if verification passed).**
   - Option A (zero-downtime): rename. `psql -c "ALTER DATABASE unifleet RENAME TO unifleet_old"`, then `psql -c "ALTER DATABASE unifleet_restore RENAME TO unifleet"`. Then redeploy the `web` service so the connection pool reconnects to the renamed DB.
   - Option B (faster, brief downtime): drop the live DB, rename the restored one. `dropdb unifleet` (with the app's read traffic stopped first), then `psql -c "ALTER DATABASE unifleet_restore RENAME TO unifleet"`. Redeploy.
   - In both cases, redeploy the `web` service to pick up the new DB name in the connection pool.

7. **Clean up.** `dropdb unifleet_old` (if you used Option A) once you're confident the new DB is good.

**Verification:** run the [§what-healthy-looks-like](#what-healthy-looks-like) smoke test list against the restored DB. All routes should work; the data should look like the backup's data, not the pre-restore data.

## 8. Secret rotation

Rotate secrets **one at a time** with a smoke test after each. If something breaks, you'll know which rotation caused it.

| Env var | What it controls | User-visible impact of rotation | Order to rotate | Smoke test after rotation |
|---|---|---|---|---|
| `DATABASE_URL` | PG connection string | None (internal) | 1st (no impact) | `/healthz` returns 200; `/api/ops/vouchers.json` returns data |
| `PERSISTENCE_BACKEND` | csv / db / pg selector | None (must stay `pg`) | 1st (no impact) | App still serves; check `audit_log` writes land in PG |
| `secret_key` | Flask session signing | **All users logged out** | 2nd (manageable impact) | `/form` still renders (no session needed); create a test session, log in, verify it works |
| `ADMIN_KEY` | Auth token for `/admin/*` | **Admin access breaks until all admin clients are updated** | 3rd (high impact) | Hit `/admin/prices` with the new `ADMIN_KEY`; expect 200 |
| `SUPPLIER_API_TOKEN` | Auth token for supplier API | **Supplier integration breaks until the supplier is told the new value** | 4th (third-party dependency) | Hit `/supplier-sheet.pdf?token=<new>`; expect 200 + valid PDF |

**The one-at-a-time rule:** if you rotate 3 secrets and the smoke test fails, you don't know which rotation caused it. Rotate one, smoke test, commit (no, the rotation is in Railway Variables, not in git), then rotate the next.

**Steps for a single rotation:**

1. Open Railway dashboard → `web` service → Variables tab.
2. Edit the variable. Paste the new value. Save.
3. **Trigger a redeploy.** Variable changes do NOT take effect until the next deploy. Use the "Deploy" button at the top of the service page.
4. Wait for "Active" status.
5. Run the smoke test for that variable (column 5 of the table above).
6. If green: rotation is done. If red: revert to the old value, redeploy, investigate.

**Ordering rationale:** `DATABASE_URL` and `PERSISTENCE_BACKEND` have no user-visible impact — start with those so you can warm up. `secret_key` and `ADMIN_KEY` have customer/admin impact — do them when you can take a brief disruption. `SUPPLIER_API_TOKEN` is third-party — schedule with the supplier's team in advance.

## 9. Local dev setup

Copy-paste runnable on a fresh Linux/macOS laptop with Docker and Poetry installed.

**One-time setup:**

```bash
# 1. Install Poetry (if not already installed)
curl -sSL https://install.python-poetry.org | python3 -

# 2. Clone the repo
git clone https://github.com/kayacohen/unifleet-v2-webapp.git
cd unifleet-v2-webapp

# 3. Install Python deps
poetry install

# 4. Copy .env.example to .env (if it exists; otherwise use the template in the Makefile)
cp .env.example .env  # then edit .env if needed
```

**Bring up the local stack:**

```bash
make up                # starts web + db containers
```

**Verify it works:**

```bash
make test-db           # runs the full pytest suite
# Expected output: "107 passed in <20s" (count is exact; time varies)
```

**Smoke-test the live local app:**

```bash
curl -I http://localhost:5000/healthz
# Expected: HTTP/1.1 200 OK
```

**Make a backup, verify it round-trips:**

```bash
mkdir -p /tmp/unifleet-backups
make backup BACKUP_DIR=/tmp/unifleet-backups
ls -lh /tmp/unifleet-backups/
# Expected: a file matching unifleet-YYYYMMDD-HHMM*.pgdump, ~20-30 KB

make restore-list BACKUP_DIR=/tmp/unifleet-backups
# Expected: TOC of the latest backup (table names + row counts)

make restore-pg BACKUP_DIR=/tmp/unifleet-backups
# Expected: unifleet_restore DB created; row counts match the source
```

**Common gotchas:**

| Symptom | Likely cause | Fix |
|---|---|---|
| `make up` hangs on `db` | Port 5432 is taken on the host (local `postgres` is the usual culprit) | Stop the host's `postgres` service: `sudo systemctl stop postgresql` |
| `make test-db` fails with "connection refused" | The `db` container isn't up yet | `make up` and wait for "healthy" in `docker compose ps` |
| `make backup` errors with "permission denied" on `data/legacy/` | The dev `data/legacy/` dir is root-owned in some envs | Use the `BACKUP_DIR=/tmp/...` override as shown above |
| `make test-db` reports fewer than 107 tests | Local `db` container is stale | `make down && make up` to start fresh |
| Asia/Manila timezone wrong in voucher output | Container is on UTC | The app uses `Asia/Manila` explicitly in `discount_store`; if you see wrong timestamps, check `date` inside the container |

## 10. On-call runbook

Decision-tree format. Read the symptom, follow the steps in order.

### §1. Service is down (5xx, no response)

1. Open Railway dashboard → `web` service → Deploys tab. Is the latest deploy red?
   - **Yes:** that deploy crashed. [§deploy-procedure](#deploy-procedure) for build/deploy error messages, or [§rollback-procedure](#rollback-procedure) if it deployed but the app crashes.
   - **No:** the latest deploy is green. Check the service's runtime logs (the "Logs" tab) for crashes.
2. Is there a crash loop in the runtime logs?
   - **Yes:** the app is starting and crashing. Usually a missing env var or a failed DB connection. Check the error message: if it mentions `_require_production_env`, an env var is missing (see [§env-var-reference](#env-var-reference) for the required list). If it mentions a DB connection error, check the `unifleet` database's status in the dashboard.
   - **No:** the app is running. Maybe the issue is with the edge / DNS. See step 3.
3. Is `unifleet.asia` (or the Railway URL) reachable from your network?
   ```bash
   curl -I https://<web-service>.up.railway.app/healthz
   ```
   - **Returns 200:** the app is fine; the issue is DNS or a customer's network. Check the DNS resolution: `dig unifleet.asia`.
   - **Times out / connection refused:** Railway might be having an outage. Check [status.railway.app](https://status.railway.app).

### §2. 5xx spike

1. Was there a deploy in the last hour?
   - **Yes:** the deploy is the most likely cause. [§rollback-procedure](#rollback-procedure).
   - **No:** the spike is not deploy-related. Continue.
2. Check the runtime logs for the spike window. Are there repeated errors with the same stack trace?
   - **Yes:** that's the root cause. Look at the function in the trace — it's a known code path. Fix the code, commit, redeploy. If you can't fix it in 30 min, [§rollback-procedure](#rollback-procedure) the most recent deploy and investigate later.
   - **No:** the errors are diverse. Likely a dependency issue (DB, network). Check the `unifleet` database's status: is it healthy, are connections OK?
3. Is the `unifleet` database's connection count maxed out?
   - **Yes:** see §4 (PG connection exhausted).
   - **No:** check the data volume. Is `/data` full? See §5 (volume full).

### §3. Backup missing (>25h since last run)

1. Open Railway dashboard → `backup` service. What's the "Last run" timestamp?
   - **> 25h ago:** the cron hasn't run. Continue.
   - **In the last 25h:** the backup ran but didn't produce a file. Check the service's logs for errors. Most common cause: `DATABASE_URL` not set on the `backup` service. The `web` service has it; the `backup` service needs its own.
2. Trigger a manual run: in the `backup` service, click the "..." → "Run command" (or use the Railway CLI as a one-off: `railway run --service backup python /app/scripts/backup_postgres.py` — but this is the only CLI escape hatch in the runbook).
3. Did the manual run succeed?
   - **Yes:** backups are running. Investigate why the cron didn't fire (check the schedule, check the `unifleet-pgdata-backups` volume's mount).
   - **No:** the error in the logs is the diagnostic. Common errors: "missing pg_dump" (the Dockerfile didn't install postgres-client — fix the Dockerfile and redeploy the `backup` service), "permission denied" on `/backups` (volume not mounted), "bad DSN" (env var issue).

### §4. PG connection exhausted

1. Open Railway dashboard → `unifleet` database → Metrics tab. What's the connection count?
   - **> 80 (Hobby plan limit is 100):** pool exhaustion. The web service's connection pool is leaking.
2. Restart the `web` service: in the service page, click the "..." → "Restart". This drops the pool and recreates it.
3. Did connections drop back to normal after the restart?
   - **Yes:** the leak was a transient (e.g., a long-running request). Monitor for an hour. If it recurs, the leak is in the code — investigate.
   - **No:** the connection pool is sized too small for the current load. Check the connection pool config in `db/pool.py`. Increase `min_size` and `max_size` if traffic warrants it.

### §5. Volume full

1. Identify which volume. The two candidates are `data` (on `web`) and `unifleet-pgdata-backups` (on `backup`).
2. For `data`: SSH into the web service (Railway shell) and run `du -sh /data/*`. The biggest directories are usually `qr_codes/` and `branded_pngs/`.
   - **QR codes or branded PNGs dominating:** these are generated assets per voucher. Old vouchers' assets can be pruned if you don't need them long-term. `find /data/qr_codes -type f -mtime +365 -delete` (or similar; adjust the age).
   - **Presets dominating:** check the per-customer preset CSVs in `/data/presets/`. Each customer has a few KB; total should be < 1 MB.
3. For `unifleet-pgdata-backups`: the backup script's retention is 14 days by default. If the volume is full, the script's rotation is broken.
   - SSH into the `backup` service (Railway shell) and run `ls -lh /backups/`. The oldest file should be no more than 14 days old.
   - If old files are accumulating, check the script's `UNIFLEET_BACKUP_RETAIN_DAYS` env var on the `backup` service. If it's set higher than 14, lower it.
   - Manually prune: `find /backups -type f -mtime +30 -delete`.

### §6. Deploy stuck

1. How long has the deploy been "Building"?
   - **< 5 min:** normal. Wait.
   - **5-10 min:** slow. Could be a slow network pulling deps, or a large `poetry.lock`. Wait a bit more.
   - **> 10 min:** stuck. Continue.
2. Cancel the deploy (click "Cancel" on the deploy row). Then re-trigger by clicking the "Deploy" button.
3. Did the new deploy succeed?
   - **Yes:** transient. Move on.
   - **No:** the stuck deploy is now a failed deploy. [§rollback-procedure](#rollback-procedure) if the previous deploy was working.

### §7. Dashboard unavailable

1. Check [status.railway.app](https://status.railway.app). Is Railway itself down?
   - **Yes:** wait. The dashboard will come back. There is no CLI fallback for deploys in this runbook (per the operator's manual-deploy decision). If you absolutely need to deploy during an outage, [contact Railway support](https://station.railway.app/) — but realistically, the right answer is "wait."
   - **No:** the issue is local (browser, network, your laptop). Try incognito, try a different network, try a different laptop.

### §8. Customer reports wrong/missing voucher

1. Pull up the voucher ID. Open the `unifleet` database's Query tab in the Railway dashboard (or `psql` from a Railway shell).
2. Run:
   ```sql
   SELECT voucher_id, status, account_code, customer_name, created_at, updated_at
   FROM vouchers
   WHERE voucher_id = '<voucher_id>';
   ```
3. Run:
   ```sql
   SELECT action, from_status, to_status, actor_ip, created_at
   FROM audit_log
   WHERE voucher_id = '<voucher_id>'
   ORDER BY created_at;
   ```
4. Cross-reference: the customer's report vs. the actual data.
   - **Voucher exists, status is what they say it is:** the customer is mistaken. Reply with the actual data.
   - **Voucher exists, status is NOT what they say it is:** someone (admin, supplier) changed it. Check the audit_log for who.
   - **Voucher doesn't exist:** it was never created, or it was deleted. Check `audit_log` for any action on this `voucher_id` (the FK is nullable; some audit rows have voucher_id that didn't survive in `vouchers`).
5. **Do NOT revert the deploy.** A customer report is not a deploy problem. The data is what it is. Reply to the customer with the audit trail.

### Escalation

If none of the above resolves the issue in 30 minutes:

1. Check [status.railway.app](https://status.railway.app).
2. If Railway is having a regional issue, [contact Railway support](https://station.railway.app/) with the deploy log + the symptom.
3. If Railway is fine, the issue is application-level. Roll back the most recent deploy (per [§rollback-procedure](#rollback-procedure)) to restore service, then investigate without customer-facing impact.

## 11. Backup verification

Verify a backup is good by restoring it into a throwaway DB and checking row counts.

**When to run this:**

- After the first nightly backup (to confirm the cron works end-to-end).
- Monthly, as a sanity check.
- After any change to the backup script (the `backup` service's image, env vars, or volume mount).

**How to run it:**

1. **Local dev (any time, no Railway needed):**
   ```bash
   make restore-list BACKUP_DIR=/path/to/your/backups
   make restore-pg BACKUP_DIR=/path/to/your/backups
   ```
   Then `psql unifleet_restore -c "SELECT count(*) FROM vouchers"` (and the other tables from [§db-restore §5](#db-restore)) and compare to the source.

2. **Production (monthly):**
   - Open Railway dashboard → `backup` service. Note the most recent backup's filename.
   - In the `web` service's Railway shell (or via a one-off `railway run`), restore into a fresh DB. The production backup file lives in the `unifleet-pgdata-backups` volume at `/backups/`. The `web` service doesn't have access to that volume; the verification has to happen in the `backup` service's shell.
   - Verify row counts (same as step 5 in [§db-restore](#db-restore)).
   - Drop the throwaway DB: `dropdb unifleet_restore`.

**Pass criteria:** all row counts match the source within a few percent. **Fail criteria:** any row count differs by more than 10%, or the restore errors out. On fail, [§db-restore](#db-restore) is the actual recovery procedure.

## 12. Env var reference

The complete list of env vars read by the app and the backup script. **Required** means the app refuses to boot without it.

### Web service (`web`)

| Name | Purpose | Required? | Example (dev) | Where to set | Last rotated |
|---|---|---|---|---|---|
| `DATABASE_URL` | Postgres connection string | **Yes** (when `PERSISTENCE_BACKEND=pg`) | `postgresql://unifleet:unifleet_dev_pw@db:5432/unifleet` | Railway → `web` → Variables | — |
| `PERSISTENCE_BACKEND` | Selects csv / db / pg | **Yes** (in production) | `pg` (or `postgres`) | Railway → `web` → Variables | — |
| `secret_key` | Flask session signing | **Yes** | (random 32+ char string) | Railway → `web` → Variables | — |
| `ADMIN_KEY` | Auth token for `/admin/*` | **Yes** | (random 32+ char string) | Railway → `web` → Variables | — |
| `SUPPLIER_API_TOKEN` | Auth token for supplier API | **Yes** | (random 32+ char string) | Railway → `web` → Variables | — |
| `UNIFLEET_DATA_DIR` | Root for generated assets | **Yes** (in production) | `/data` | Railway → `web` → Variables | — |

### Backup service (`backup`)

| Name | Purpose | Required? | Example (dev) | Where to set | Last rotated |
|---|---|---|---|---|---|
| `DATABASE_URL` | Postgres connection string (for `pg_dump`) | **Yes** | (same as `web`'s) | Railway → `backup` → Variables | — |
| `UNIFLEET_BACKUP_DIR` | Where to write `.pgdump` files | No (default `/backups`) | `/tmp` (for local test) | Railway → `backup` → Variables | — |
| `UNIFLEET_BACKUP_RETAIN_DAYS` | Rotation threshold | No (default `14`) | `30` | Railway → `backup` → Variables | — |
| `UNIFLEET_BACKUP_S3_BUCKET` | S3 off-platform upload (optional) | No | (bucket name) | Railway → `backup` → Variables | — |
| `UNIFLEET_BACKUP_S3_PREFIX` | S3 key prefix (optional) | No | `unifleet/` | Railway → `backup` → Variables | — |
| `UNIFLEET_BACKUP_S3_STORAGE_CLASS` | S3 storage class (optional) | No (default `STANDARD_IA`) | `GLACIER_IR` | Railway → `backup` → Variables | — |
| `AWS_ACCESS_KEY_ID` | AWS creds for S3 (optional) | No (required only if S3 is used) | (AWS access key) | Railway → `backup` → Variables | — |
| `AWS_SECRET_ACCESS_KEY` | AWS secret for S3 (optional) | No (required only if S3 is used) | (AWS secret) | Railway → `backup` → Variables | — |

### Cross-check: env vars in code but not in this table

If a code change adds a new `os.environ.get(...)` call, **add the env var to this table in the same PR.** Verification is in [§T4 of the plan](#) (T4 walks this table against the codebase).

---

## 13. Why this runbook doesn't have X

The original project plan included a "production-readiness" phase (F3.1-F3.7) covering: mandatory env-driven secrets with a boot-time gate, CSRF protection on form endpoints, structured logging, dedupe helper functions, the `/discount-locator` route decision, and turning `ENFORCE_PHASES` on by default. **That work has been permanently deferred** per the operator's decision to ship the runbook as-is and harden later if needed.

What this means operationally:

- The app currently reads env vars but does **not** refuse to boot on missing required env vars. If you delete `secret_key` from the Variables tab, the app starts and serves requests with an empty session signing key (insecure but functional). Mitigation: the env-var table in §12 is the operator's checklist for required vars. Don't skip rows marked **Yes**.
- The app does **not** have CSRF protection on `/book`, `/register`, `/admin/*`, `/ops/...` POST handlers. For a low-traffic internal app this is acceptable risk; for a public-facing app with real customer PII, it would not be. Add CSRF if the threat model changes.
- Logs are ad-hoc `print()` and string concatenation, not structured `logging`. Railway's log capture works, but grep'ing is harder than it would be with `logging` + request IDs.
- The `ENFORCE_PHASES` flag defaults to **off**. Phase ordering (Redeemed → Unredeemed is illegal, etc.) is not enforced server-side. The Flask routes assume the operator doesn't click the wrong button. This is the same risk as the original Replit deployment.

If any of these become a problem, the remediation is: add the F3.x feature, update this runbook, redeploy. The pattern is the same as any other code change.
