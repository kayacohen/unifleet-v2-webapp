# Plan: F4 — Railway operations runbook + quickref (manual deployment)

> **Date:** 2026-06-06
> **Project source:** `specs/plans/PROJECT-migrate-to-railway.md` (F4.4 + F4.5), `specs/plans/PLAN-pg-backup.md` (DB-restore procedure referenced)
> **Estimated tasks:** ~6 (4 doc-writing tasks + 2 verification tasks)
> **Planning session:** detailed

## Summary

Two markdown docs in the repo (`docs/quickref.md` ~80 lines, `docs/runbook.md` ~500 lines) that an operator can follow to manually deploy, monitor, roll back, rotate secrets, and restore the DB on Railway — without tribal knowledge, without a CI/CD pipeline, and without writing any new application code. The deployment trigger is the Railway dashboard "Deploy" button (no CLI, no `deploy` branch). The runbook is the source of truth; the quickref is a 1-page cheatsheet of "if X, do Y" — the two are kept in sync. Out of scope: any new application code, CI/CD, third-party APM, PagerDuty, status page.

## Requirements

### Functional Requirements
1. `docs/quickref.md` exists and is ≤ 100 lines: a one-page operator cheatsheet of the most common scenarios (deploy, rollback, smoke-test, backup missing, secret rotation, DB restore pointer).
2. `docs/runbook.md` exists and is ≤ 700 lines: a structured operator manual with these sections — Topology, Dashboard bookmarks, Deploy procedure, What "healthy" looks like, Monitoring, Rollback, DB restore, Secret rotation, Local dev setup, On-call runbook, Backup verification, Env var reference.
3. Both docs are checked into the repo on `main` (public; this repo is already public on GitHub) and are the canonical place an operator looks first.
4. The deploy procedure documents the **dashboard "Deploy" button** workflow (no CLI, no special branch, no CI). It covers: the exact button location, the order of operations, the post-deploy smoke test, the "is it healthy?" check.
5. The rollback procedure documents the Railway dashboard "Redeploy previous" workflow, including the decision rule for "is the new deploy actually broken, or is it an unrelated incident?" and the "previous" of last-resort fallback (the last known-good commit SHA).
6. The DB-restore section reproduces the 6-step procedure from `PLAN-pg-backup.md` in operator-friendly language (no SQL the operator has to reconstruct, no `psql` shell sessions that hang).
7. The secret-rotation section lists every env var the app needs, in deployment order (which to rotate first to avoid downtime), with the "verify it still works" smoke test for each.
8. The local-dev-setup section is copy-paste runnable on a fresh laptop: Poetry install, `.env.example` copy, `make up`, `make test-db`, `make backup` round-trip.
9. The on-call runbook is the "if X happens, do Y" decision tree for the ~8 most likely production failure modes (service down, 5xx spike, backup missing, PG connection exhausted, volume full, deploy stuck, dashboard unavailable, customer reports a wrong voucher).
10. The env-var reference is a single table: name, purpose, where to set it, whether the app refuses to boot if missing, example value (dev only — never prod).

### Non-Functional Requirements
1. Both docs are pure markdown, render correctly on GitHub, and have a table of contents at the top of the runbook.
2. The quickref fits on a printed page (operator can print it and pin it next to the laptop).
3. The runbook is organized so the operator can `grep` for the symptom they're seeing (e.g., `grep -n "5xx" docs/runbook.md` returns the right section).
4. No doc content references specific secret values (only env-var names and dev-only example values).
5. The runbook is reviewed for accuracy against the current code: the env-var list matches what `main.py` actually reads; the smoke-test routes match the current route inventory; the backup commands match `scripts/backup_postgres.py`; the Makefile targets match what `make help` shows.

## Behaviors

### Doc structure: runbook is source of truth, quickref is the cheatsheet

**Why rules matter:**
- A single canonical doc (`runbook.md`) is easier to keep in sync than multiple files. A solo operator doesn't have time to maintain 4 doc files.
- The `quickref.md` is a derivate — it links into the runbook for full context. If they ever disagree, the runbook wins. This rule is stated at the top of both files.
- A 1-page cheatsheet that fits on a printed page is the difference between "I can answer this in 5 seconds" and "I'm reading docs while the customer is on the phone."

**What's optional vs required:**
- Required: a single canonical source of truth, a single page cheatsheet, a clear "runbook wins" rule.
- Optional: per-section deep-dive docs (e.g., a separate `docs/db-restore-deep-dive.md`). Not in scope this round.

**Common mistakes:**
- Duplicating content between the two files. The quickref is a TABLE; the runbook is the FULL TEXT. Don't write prose in the quickref.
- Stale quickref. If the runbook changes a deploy step, the quickref's "deploy" row must change too. Capture this as a verification task.
- Linking to a Notion page or external wiki. The docs are in the repo. Operator can find them offline (e.g., on a plane).

### Deploy procedure: dashboard "Deploy" button only

**Why rules matter:**
- "Manual" means the operator triggers the deploy themselves. A dashboard button is the most visible, most reproducible, most auditable way to do that without writing CLI scripts.
- No CLI = no "what version of `railway` is on this laptop?" problem. No `deploy` branch = no "did I push to the right branch?" problem.
- The button has its own audit trail (who clicked, when, what commit). For a solo operator this is sufficient.

**What's optional vs required:**
- Required: the operator can describe the deploy in 5 steps: push to main, open dashboard, click Deploy, watch logs, smoke test.
- Optional: a CLI fallback. Not in scope. (If the dashboard is unavailable, the operator uses the Railway status page to find the right contact, not `railway up` from a random laptop.)

**Common mistakes:**
- Clicking Deploy and walking away. The deploy takes 1-3 minutes. Operator watches the logs the whole time.
- "It deployed green, must be fine." The smoke test is required, not optional. Catches env-var misses, broken migrations, and dangling imports.
- Reverting to an OLDER deploy when the CURRENT deploy is actually fine. The rollback decision rule is: "did the smoke test fail? yes → revert. did the smoke test pass but a customer reported a bug? no → investigate, do not revert."

### Monitoring: dashboard-only, no APM this round

**Why rules matter:**
- Railway's Hobby plan exposes CPU%, memory%, request count, response time, and PG storage + connections in the dashboard. That's enough for a low-traffic fuel-voucher app.
- Adding a third-party APM (Datadog, Sentry, New Relic) is a 2-4 hour integration per tool, ongoing cost ($0-50/mo for the volume of traffic this app has), and a new failure mode (the APM itself goes down). Not justified for the current scale.
- The operator can check the dashboard 2-3 times a day, which is the right cadence for this app's traffic.

**What's optional vs required:**
- Required: the runbook documents which dashboard panels to look at and what "normal" looks like for each metric.
- Optional: automated alerts. Captured as a follow-up task (see "Open Questions" below) — not in scope this round.

**Common mistakes:**
- "I should set up Datadog." No, not for this app's scale. The dashboard is enough.
- "I should write a Grafana dashboard." No, see above.
- "I'll check it once a day." For the first week after cutover, check 2-3x per day. After that, once a day is fine.

### Rollback: previous successful deploy in Railway history

**Why rules matter:**
- Railway keeps the last 5-10 successful deploys in the dashboard (Settings → Deploys → History). Reverting to any of them is a one-click action.
- This is faster than a DB restore (seconds vs. minutes) and is the right tool for "the new code is broken, the old code was fine."
- A DB restore is only needed for "the new code corrupted the DB" or "we need yesterday's data, not last week's."

**What's optional vs required:**
- Required: the runbook documents the exact dashboard path, the smoke-test sequence, and the "this might not fix it" warning for cases where the data is corrupted.
- Optional: a pre-baked "rollback commit" (e.g., a `rollback` branch). Not in scope.

**Common mistakes:**
- Reverting without a smoke test. Always smoke-test after reverting — the "previous" might also be broken.
- Reverting twice in a row. If two consecutive reverts are needed, the problem is probably data, not code. Stop and investigate.
- Forgetting to commit the revert. Reverting in the dashboard doesn't create a git commit. If the operator wants the revert to be permanent, they have to `git revert` locally and push.

### DB restore: copy of the 6-step procedure from PLAN-pg-backup.md

**Why rules matter:**
- The procedure is already documented in the plan (PLAN-pg-backup.md §Restore procedure). The runbook reproduces it in operator-friendly language with the actual commands inline (no "see also" links to the plan).
- Operators should be able to restore the DB without having to read a plan document. The plan is for planning; the runbook is for executing.
- RTO is < 30 minutes per the backup plan. The runbook should be runnable in 30 minutes by a focused operator.

**What's optional vs required:**
- Required: the runbook section has the actual commands inline, in the exact order, with the verification step at the end.
- Optional: an automation script (e.g., `make restore-latest`). The backup tool already has a `make restore-pg` target; the runbook documents that target and links to it.

**Common mistakes:**
- Restoring into the live DB instead of a test DB first. The runbook explicitly says: "restore into `unifleet_restore` first, verify row counts, THEN promote to live."
- Restoring the wrong backup file. The runbook explicitly says: "always `make restore-list` first to see what's in the backup volume."
- Forgetting to verify after restore. The row-count check at the end is non-negotiable.

### Secret rotation: env-var-by-env-var, with smoke test per rotation

**Why rules matter:**
- Railway env vars are the ONLY place secrets live (per the project's "no hardcoded secrets" rule from F3.1). Rotation = edit the var in the dashboard, redeploy, smoke test.
- The order matters: rotating `secret_key` invalidates all active Flask sessions (users get logged out). Rotating `ADMIN_KEY` invalidates admin access. Rotating `SUPPLIER_API_TOKEN` breaks the supplier API until the supplier is told the new value. The runbook calls out the user-visible impact of each rotation.
- A "smoke test per rotation" catches the case where one of the rotations breaks a route that the others don't cover.

**What's optional vs required:**
- Required: the runbook lists every env var, its user-visible impact, and the smoke test for it.
- Optional: a secret-management tool (1Password CLI, Doppler, etc.). Not in scope.

**Common mistakes:**
- Rotating multiple secrets at once. If something breaks, you don't know which one caused it. Rotate one at a time.
- Forgetting to tell the supplier about a new `SUPPLIER_API_TOKEN`. The supplier integration is a third party; they need to update their config.
- Editing the variable without redeploying. Variables don't take effect until the next deploy. The runbook explicitly says: "edit, save, then click Deploy."

### Local dev setup: copy-paste runnable on a fresh laptop

**Why rules matter:**
- The operator (or a future operator) needs to be able to develop locally without depending on production access. The setup must work end-to-end on a fresh Linux/macOS laptop with Docker + Poetry installed.
- "Works on my machine" is the wrong test. The right test is "a fresh laptop can run this in 30 minutes without help."

**What's optional vs required:**
- Required: the commands are copy-paste runnable. The output of each command is documented ("you should see: '107 passed in 14s'"). The common failure modes are listed ("if `make up` hangs on `db`, check if port 5432 is taken on the host — local `postgres` is the usual culprit").
- Optional: a one-liner install script (e.g., `bin/setup.sh`). The Makefile is the install script.

**Common mistakes:**
- "I documented it in my head, I'll remember." Write it down.
- "The README covers it." The README covers project overview; the runbook covers operations. They are different docs.
- "It works on my Mac, must work on Linux." Docker makes this mostly true, but timezone (Asia/Manila) is a common gotcha.

### On-call runbook: ~8 scenarios, decision-tree format

**Why rules matter:**
- The operator is one person. When the phone rings at 2 AM, they need a decision tree, not a novel. "If you see X, do Y. If Y doesn't work, do Z. If Z doesn't work, escalate to [contact]."
- The 8 scenarios are the most likely failure modes for this specific app, derived from the backup plan, the project plan, and the actual code. Not a generic "monitoring runbook" template.

**What's optional vs required:**
- Required: 8 scenarios, each with a 3-5 step decision tree, each cross-linked to the relevant runbook section.
- Optional: a 9th "everything is on fire" escalation path. Listed as a follow-up (contact Railway support).

**Common mistakes:**
- Generic scenarios. "Service is down" is too vague. "Web service shows `CrashLoopBackOff` in the deploy logs" is specific.
- No escalation path. "Try harder" is not a plan. The runbook explicitly says: "if X, contact [name] at [contact info]."
- Not testing the runbook. The runbook is a script. Run it once on a quiet day to make sure the commands work.

## Detailed Specifications

### `docs/quickref.md` (≤ 100 lines)

**Purpose:** 1-page operator cheatsheet. Print and pin next to the laptop.

**Interface:**
- A single markdown file in the repo root: `docs/quickref.md`
- Linked from the runbook's first line
- Linked from the project README (Operator's quick reference)

**Behavior:**
- Section 1: "If you see X, do Y" — a 8-10 row table. Columns: Symptom, First action, Where to go next.
- Section 2: Top 5 env vars you'll touch most often. Columns: Name, Where to set it, What it does.
- Section 3: 3 commands you'll run most often. With the exact command, the exact expected output, and the "if it doesn't match, check the runbook" pointer.

**Validation Rules:**
- Length: 100 lines max.
- No prose paragraphs. Tables only.
- Every row links to a runbook section by anchor (e.g., `[Rollback procedure](#rollback-procedure)`).

**Error Scenarios:**
| Condition | Expected Behavior |
|-----------|-------------------|
| Quickref disagrees with runbook | Runbook wins. Update the quickref. |
| Quickref row has no runbook section | Don't add the row. |
| Quickref grows past 100 lines | Cut content. The runbook is the long form. |

### `docs/runbook.md` (≤ 700 lines)

**Purpose:** Canonical operator manual. The single source of truth for deploying, monitoring, rolling back, restoring, and rotating secrets on Railway.

**Interface:**
- A single markdown file: `docs/runbook.md`
- Table of contents at the top (anchors to each section)
- Cross-references the quickref (`[see quickref §deploy](#deploy)`)

**Behavior — sections in order:**

1. **Topology** (≤ 20 lines) — Project name, service names (`web`, `backup`), database name (`unifleet`), volume names (`data` mounted at `/data`, `unifleet-pgdata-backups` mounted at `/backups`), region (`asia-southeast`), domain (`unifleet.asia`). One ASCII diagram.

2. **Dashboard bookmarks** (≤ 20 lines) — Direct URLs to: project root, web service deploys history, web service variables, web service metrics, backup service last run, PG database metrics. Operator bookmarks all of these on day 1.

3. **Deploy procedure** (≤ 80 lines) — 5 steps: (1) push to `main`, (2) open dashboard → web service, (3) watch deploy logs (last 50 lines), (4) curl /healthz, (5) smoke test. With the "is it healthy?" check and the "what if it's not" decision rule.

4. **What "healthy" looks like** (≤ 60 lines) — 6-8 smoke-test routes (the ones from APP_REPORT.md + the F4.3 inventory): `/healthz`, `/form`, `/book`, `/redeem`, `/admin/prices`, `/api/ops/vouchers.json`, `/assets/qr/<vid>.png`. Expected response per route. What to do if any of them is slow or 5xx.

5. **Monitoring** (≤ 50 lines) — Dashboard panels: web service CPU/memory/request count/response time, PG storage/connections, data volume free space, backup volume free space. What's normal. What's a warning sign. How often to check.

6. **Rollback procedure** (≤ 60 lines) — Dashboard path, smoke-test sequence, the decision rule (broken deploy vs. unrelated incident), the "previous" fallback, when a rollback is the wrong tool (data corruption → use §DB restore).

7. **DB restore** (≤ 100 lines) — Reproduces the 6-step procedure from `PLAN-pg-backup.md` inline. Pre-flight: `make restore-list`. Restore into `unifleet_restore` first. Verify row counts. Promote to live. RTO < 30 min. RPO < 24 h. The exact commands.

8. **Secret rotation** (≤ 100 lines) — Table: env var name, purpose, user-visible impact, smoke test, where to set it. Order of rotation: `DATABASE_URL` (no impact) → `secret_key` (logs everyone out) → `ADMIN_KEY` (breaks admin until updated) → `SUPPLIER_API_TOKEN` (breaks supplier integration until supplier is told). One-at-a-time rule.

9. **Local dev setup** (≤ 80 lines) — Step-by-step. Install Poetry. Copy `.env.example` to `.env`. `make up`. `make test-db`. Expected: "107 passed in 14s". `make backup` round-trip. Common gotchas (port 5432, timezone, Mac M1 docker).

10. **On-call runbook** (≤ 100 lines) — 8 scenarios in decision-tree format: (1) service down, (2) 5xx spike, (3) backup missing, (4) PG connection exhausted, (5) volume full, (6) deploy stuck, (7) dashboard unavailable, (8) customer reports wrong voucher. Each scenario: 3-5 step decision tree. Escalation path at the bottom.

11. **Backup verification** (≤ 30 lines) — How to manually verify a backup is good: `make restore-list`, then `make restore-pg` into a throwaway DB, then `psql` row counts. When to run this: after the first nightly backup, then monthly.

12. **Env var reference** (≤ 50 lines) — Table: name, purpose, required? (does the app refuse to boot?), example dev value, where to set it, last-rotated date.

**Validation Rules:**
- Every section is anchored (H2 headers).
- Every cross-reference uses anchors (`#section-name`), not full URLs.
- Length: 700 lines max. If a section needs more, it's a sign the section should be split (but split is out of scope this round).

**Error Scenarios:**
| Condition | Expected Behavior |
|-----------|-------------------|
| Runbook command fails | Operator falls back to: check the error in the deploy logs, then check the backup plan, then escalate. |
| Dashboard is down | Railway status page → if regional, wait. If global, contact support. |
| Operator has never seen this failure mode | Add it to the on-call runbook as scenario #9+. |

## Key Constraints

| Constraint | Why It Matters |
|------------|----------------|
| Deploy is via the Railway dashboard "Deploy" button only — no CLI, no special branch, no CI | User explicitly chose "Dashboard Deploy button only" for manual deploys. The runbook documents that exact workflow. |
| No new application code in this feature | The runbook documents existing capabilities (the backup tool, the data_paths, the PERSISTENCE_BACKEND switch) but does not add new code, new env vars, or new endpoints. |
| The quickref is a TABLE, not prose | A 1-page cheatsheet must fit on a printed page. Prose blows the line budget. |
| The runbook is the source of truth, the quickref is the cheatsheet | If they disagree, runbook wins. Stated at the top of both files. |
| The DB-restore section reproduces the 6-step procedure inline | The plan (`PLAN-pg-backup.md`) is for planning; the runbook is for executing. Operators should be able to restore the DB without opening a plan document. |
| The runbook is checked into the repo on `main` | It's in the repo so it can be versioned, reviewed in PRs, and accessed offline. |
| The env-var reference lists every var `main.py` reads | If the list is incomplete, the operator will rotate a var that exists in code but isn't documented. Caught by the verification task. |
| No monitoring/alerting is implemented in this feature | The runbook DOCUMENTS what to watch in the dashboard, but does not SET UP automated alerts. That's a follow-up. |
| The on-call runbook is ~8 scenarios, not exhaustive | The 8 scenarios cover the most likely failure modes for this specific app. A 50-scenario template would be unreadable. |

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|----------|----------|-----------|
| Operator is asleep when service goes down | Documented in on-call runbook §1: check the deploy logs, redeploy previous. No automated page (no PagerDuty). The 2 AM page is the operator's phone, which they accept as a trade-off for not having a third-party APM. |
| Backup cron service silently fails for 3 days | On-call runbook §3: check "Last run" timestamp in dashboard. Manually trigger `railway run --service backup python scripts/backup_postgres.py` (per `PLAN-pg-backup.md`). If that fails, restore script's "missing pg_dump" error path applies. |
| A deploy succeeds but a customer reports a bug 3 hours later | On-call runbook §8: check audit_log, check the diff, do NOT revert. The "rollback" decision rule explicitly says: do not revert if smoke tests passed and the issue is data-related, not code-related. |
| The Railway dashboard itself is down | Runbook §Deploy / §Rollback both have a "if the dashboard is unavailable, check the Railway status page" fallback. No CLI fallback in scope (per the user's manual-deploy choice). |
| The PG volume is full | On-call runbook §5: check free space, prune audit_log rows older than X, contact Railway for volume resize. The volume is on Hobby plan (5 GB default); if the app outgrows that, the operator either resizes (paid) or archives old audit logs. |
| A secret rotation breaks a route that wasn't smoke-tested | On-call runbook §7: rotate one secret at a time (per §Secret rotation), smoke test after each. If the rotation broke something, the smoke test catches it. The "rotate one at a time" rule is in §Secret rotation. |
| The operator has never seen a particular failure mode | The runbook is intentionally not exhaustive. Operator adds new scenarios to the on-call runbook as they happen. Each new scenario is a doc-only PR (no code). |
| `make restore-list` shows no backups | The backup cron is broken. On-call runbook §3 applies. The runbook is runnable even with zero backups (the "no backups" case is the worst case, and the runbook documents it). |
| The smoke test passes but the QR codes are missing | Runbook §What "healthy" looks like explicitly includes `/assets/qr/<vid>.png` as one of the 6-8 routes. If QR codes are missing, the data_paths Volume mount is wrong. Fix: check the `data` Volume is mounted at `/data` in the dashboard. |
| The operator deploys but the new code needs an env var that wasn't set | On-call runbook §6 (deploy stuck): the deploy logs will show an `_require_production_env()` failure (per the project's mandatory-env rule from F3.1). Fix: add the env var in the dashboard, redeploy. |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|------------------------|----------------|
| 1 | Doc structure: `runbook.md` + `quickref.md` | One big doc, multiple split docs | User chose "runbook + quickref". Single source of truth + 1-page cheatsheet is the right shape for a solo operator. |
| 2 | Deploy trigger: Railway dashboard "Deploy" button | CLI (`railway up`), special `deploy` branch, CI on push to `main` | User chose "Dashboard Deploy button only". No CLI = no laptop-specific failures. No special branch = no "wrong branch" failures. |
| 3 | No CI/CD this round | GitHub Actions, Railway's built-in CI | User said "deploy manually" — explicit. CI is a follow-up if the operator wants it later. |
| 4 | No third-party APM this round (Datadog, Sentry, etc.) | Add Sentry for error tracking, add Datadog for APM | App is low-traffic, the dashboard is enough. Adding APM is a 2-4 hour integration + ongoing cost. Captured as a follow-up. |
| 5 | No automated alerts this round | Slack webhook, email, PagerDuty | Solo operator, no escalation path needed. Dashboard-only check is sufficient at current scale. Captured as a follow-up. |
| 6 | The on-call runbook is 8 scenarios, not exhaustive | 50-scenario template, generic monitoring runbook | 8 scenarios is the right size for this specific app. A template is unreadable. New scenarios are added as doc-only PRs when they happen. |
| 7 | The runbook reproduces the DB-restore procedure inline | Link to `PLAN-pg-backup.md` | The plan is for planning, the runbook is for executing. Operators shouldn't have to open a plan doc during an incident. |
| 8 | The env-var reference is a table, not a narrative | A section per env var with prose | A table is scannable. The operator rotates 1 var at a time and needs to see all of them at once. |
| 9 | The runbook is checked into the repo, not a wiki | Notion, Confluence, GitHub Wiki | The repo is the canonical place. Wiki would drift. Operator can find it offline. |
| 10 | The local dev setup is a copy-paste runnable section, not a "see README" pointer | Link to README | The README is project overview; the runbook is operations. Different docs. The operator on a fresh laptop reads the runbook, not the README. |
| 11 | The quickref is a TABLE only, no prose | Prose intro + table | The line budget is 100 lines. Prose eats the budget. Tables are scannable. |
| 12 | Each on-call scenario is a 3-5 step decision tree | A paragraph per scenario | A solo operator at 2 AM reads decision trees, not paragraphs. The "if X, do Y" format is non-negotiable. |

## Scope Boundaries

### In Scope
- `docs/quickref.md` (new file, ~80 lines, table-only)
- `docs/runbook.md` (new file, ~500 lines, structured sections)
- README update: add a "Operator's quick reference" link to `docs/quickref.md` (1-line change, not a README rewrite)
- Verification task: walk through every command in the runbook, confirm it works against the local Docker stack (catches stale commands)
- Verification task: walk through the env-var reference, confirm it matches what `main.py` actually reads (catches missing vars)

### Out of Scope
- Any new application code (no new endpoints, no new env vars, no new Makefile targets, no new scripts)
- CI/CD (GitHub Actions, pre-deploy tests)
- Third-party APM (Datadog, Sentry, New Relic)
- Automated alerts (Slack, email, PagerDuty)
- Multi-region deployment
- Auto-scaling / load balancer config
- A separate "deployment runbook" for Replit (Replit is being decommissioned in F3 per the operator's plan)
- A 9th+ on-call scenario template (scenarios are added on-demand, not pre-baked)
- Disaster recovery beyond the backup tool (no geo-redundancy)
- Status page for end-users
- README rewrite (the operator wants a 1-line link, not a full README overhaul)
- Handoff to a future operator (the runbook IS the handoff — no separate onboarding doc)
- Per-env (staging/prod) distinction (this app has one env: production)

## Dependencies

### Depends On (must exist before this work starts)
- `scripts/backup_postgres.py` + `Dockerfile.backup` + Makefile targets (commit `068a04d`) — needed for the runbook's §DB restore and §Backup verification sections to be accurate
- `data_paths.py` (commit `1c4b9b6`) — needed for the runbook's §Local dev setup (`UNIFLEET_DATA_DIR` env var)
- `app_recovery.md` and `APP_REPORT.md` — needed for the runbook's §What "healthy" looks like (smoke-test routes)
- `main.py` (current state on `main`) — needed for the runbook's §Env var reference (every env var `main.py` reads must be listed)

### Depended On By (other work waiting for this)
- The actual F3 cutover (now operator-owned): the runbook is the operator's playbook for the cutover. Without the runbook, the operator is doing the cutover from memory.
- F4.8 (decommission Replit): the runbook's §Monitoring and §On-call runbook are the pre-conditions for confidently turning Replit off.
- Future F5+ work (per the original project plan): any new feature work needs the runbook to be the canonical "how do I deploy this?" doc.

## Architecture Notes

- Both docs are plain markdown. No build step, no doc generator, no linting. `git diff` is the review tool.
- The runbook is the single source of truth. If the quickref disagrees with the runbook, the runbook wins. This rule is stated at the top of both files.
- The env-var reference is the ONLY place in the repo where the env vars are enumerated. If `main.py` adds a new env var, the runbook must be updated in the same PR. (Captured as a verification task: when adding new env vars, the runbook is a required review item.)
- The on-call runbook is a living document. New scenarios are added as doc-only PRs. Each scenario is reviewed by the operator themselves (no second pair of eyes needed for a solo operator).
- The runbook is the operator's first read on day 1. It's also the operator's reference at 2 AM. Both audiences shape the format: structured for grep, decision-tree for fast action, table for env vars.

## Open Questions (if any)

- **Q: Should the runbook be public on GitHub?**
  - **Impact if unresolved:** If yes, the env-var NAMES (not values) are public, which is fine. If no, the doc lives elsewhere — defeats the "in the repo" decision.
  - **Suggested default:** Public. The repo is already public. The env-var names are not secrets. The doc structure is not sensitive.
- **Q: When should the runbook be reviewed?**
  - **Impact if unresolved:** A stale runbook is worse than no runbook.
  - **Suggested default:** Review the runbook once after the first deploy (catch any command that's wrong). Review again after the first incident (catch any missing scenario). After that, review when the code changes (the env-var list in particular).
- **Q: Should the runbook include a section on the project's "skip hardening permanently" decision?**
  - **Impact if unresolved:** A future operator (or the user themselves in 6 months) might wonder why there's no CSRF, no structured logging, no CI. The runbook is a good place to record that decision.
  - **Suggested default:** Yes, add a short "Why this runbook doesn't have X" section at the bottom, calling out the deferred F3.1-F3.7 hardening and the rationale (operator chose to ship and harden later if needed). 5-10 lines.

---
_This plan is the input for the generate-tasks skill._
_Review this document, then run: "Generate task from plan: specs/plans/PLAN-railway-ops-runbook.md"_
