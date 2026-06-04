# Project Plan: UniFleet Migration to Railway + Postgres

> **Date:** 2026-06-04
> **Type:** migration (with cleanup)
> **Estimated features:** 27
> **Estimated phases:** 4

## Project Summary

Migrate the live UniFleet v2 webapp from Replit to Railway, swap the CSV-based persistence layer for Postgres, attach a Railway Volume for generated voucher assets, and point the `unifleet.asia` domain at the new host. The migration also addresses a backlog of production-readiness issues (hardcoded secrets, broken route, missing tests, duplicated helpers) that the team does not want to carry into the new environment. "Done" means a live, deployable system on `unifleet.asia` with no CSV files in the request path, all real customer/audit/voucher data carried over, and a written runbook another operator can follow to ship changes.

## System Boundaries

### In Scope
- Re-hosting the existing Flask monolith on Railway (service, Postgres, Volume, domain)
- Replacing the CSV master ledger, JSON sidecar stores, and CSV audit log with Postgres tables
- Migrating live data from Replit to the new Postgres + Volume
- Resolving the production-readiness issues called out in `APP_REPORT.md` (secrets, broken route, tests, helpers, CSRF)
- DNS cutover for `unifleet.asia` and decommissioning of the Replit project
- Deployment and operational documentation

### Out of Scope
- New product features (e.g., mobile app, supplier webhooks, automated invoicing)
- Multi-tenant separation (UniFleet remains a single-tenant app)
- Migration to a different language/framework (e.g., FastAPI, Django) — this is a hosting + data-layer migration, not a rewrite
- Migration to object storage (S3/R2) — Railway Volume is sufficient and chosen deliberately
- Replacement of Jinja templates with a SPA frontend
- Real user authentication (sessions, login, RBAC) — deferred to a follow-up project; the existing key-gates remain

### External Integrations
- **GitHub** — source control; Railway deploys on push to `main` (or selected branch)
- **Railway** — hosting, managed Postgres, Volume, TLS, build pipeline
- **Domain registrar (for `unifleet.asia`)** — DNS A/CNAME record pointed at Railway
- **Downstream fuel supplier** — unchanged consumer of `/supplier-api/<voucher_id>` and `/export_supplier_csv`; these endpoints are stable contracts
- **UptimeRobot (or equivalent)** — health check consumer of `/healthz`

## Architecture Direction

### High-Level Structure

```
                        ┌────────────────────────┐
                        │   unifleet.asia (DNS)  │
                        └──────────┬─────────────┘
                                   │ HTTPS
                                   ▼
                        ┌────────────────────────┐
                        │  Railway (TLS, proxy)  │
                        └──────────┬─────────────┘
                                   │
                                   ▼
   ┌──────────────────────────────────────────────────────┐
   │   Railway Service: gunicorn → Flask app (main:app)   │
   │                                                      │
   │  ┌────────────┐  ┌──────────────┐  ┌──────────────┐  │
   │  │ Public     │  │ Admin/Ops    │  │ Supplier     │  │
   │  │ HTML routes│  │ key-gated    │  │ token-gated  │  │
   │  └─────┬──────┘  └──────┬───────┘  └──────┬───────┘  │
   │        │                │                 │          │
   │        └────────────────┼─────────────────┘          │
   │                         ▼                            │
   │              ┌──────────────────────┐                │
   │              │  PostgresRepo        │                │
   │              │  (full Repo iface)   │                │
   │              └──────────┬───────────┘                │
   │                         │                            │
   │       ┌─────────────────┼─────────────────┐          │
   │       ▼                 ▼                 ▼          │
   │  Postgres tables:  vouchers, customers, stations,    │
   │  presets, prices, discounts, audit_log               │
   └──────────────────────────────────────────────────────┘
                                   │
                                   ▼ (read/write generated files)
                        ┌────────────────────────┐
                        │  Railway Volume        │
                        │  (QR/PNG, PDFs, fonts) │
                        └────────────────────────┘
```

### Key Technology Choices

| Choice | Decision | Rationale |
|---|---|---|
| Hosting | Railway | User decision; aligns with Postgres + Volume + simple GitHub deploy |
| Database | Postgres (managed via Railway) | Production-grade, ACID, supports the relational schema (vouchers ↔ customers ↔ stations), gives us a real migration story instead of the half-built SQLite path |
| File storage | Railway Volume | Single-instance, simplest persistent disk for generated PNGs/PDFs; object storage deferred |
| WSGI | Gunicorn | Already in use on Replit; same `main:app` target works on Railway |
| Frontend | Jinja2 (unchanged) | Server-rendered monolith remains; no SPA work in this project |
| CI/CD | Railway GitHub deploy integration | Push-to-deploy; no separate GH Actions runner needed for MVP |
| Secrets | Railway env vars | Mandatory at boot; no in-source defaults for `ADMIN_KEY`, `SUPPLIER_API_TOKEN`, `secret_key` |
| Timezone | `zoneinfo.ZoneInfo("Asia/Manila")` | Stdlib, no extra dep; consistent with current code |
| Testing | `pytest` + minimal CI gate | First test suite in the project; covers pricing math, lifecycle, repo round-trip |
| Package management | Poetry (unchanged) | Already declared; Railway can build from `pyproject.toml` |
| Linting/typing | `ruff` + `pyright` (unchanged) | Already configured in `pyproject.toml` |

### Patterns & Conventions

- **Repository pattern (existing)** — `Repo` interface stays; `CSVRepo` is deleted after Phase 2 and replaced by a complete `PostgresRepo`. No call sites change beyond the backend selector.
- **Snapshot-on-book, freeze-on-approve pricing (existing)** — preserved; pricing math tests land in Phase 3.
- **Atomic file writes (existing)** — preserved for the Volume path; not relevant once JSON sidecar stores move to Postgres.
- **Audit log as a first-class table** — replaces the current flat `ops_audit_log.csv`; same column shape, new queryability.
- **No new architectural patterns** — this project is a hosting + persistence swap + cleanup, not a pattern introduction. New patterns (e.g., real auth, async workers) belong in follow-up projects.
- **Phase gate at boot** — production refuses to start if any of the mandatory env vars are missing or set to a known default value. This is enforced in a single `_require_production_env()` helper called at app startup.

## Feature Map

### Feature List

| # | Feature | Type | Description | Dependencies |
|---|---------|------|-------------|--------------|
| F1.1 | Railway service + Postgres + Volume | infrastructure | Provision the project, service, managed Postgres DB, and an attached Volume; verify shell connectivity | None |
| F1.2 | Domain binding for `unifleet.asia` | infrastructure | Configure DNS, verify Railway-provisioned TLS cert, smoke-test the hostname | F1.1 |
| F1.3 | Env-var management | infrastructure | Move all configurable values to Railway env vars; produce a `.env.example` for local dev | F1.1 |
| F1.4 | CI/CD: Railway GitHub deploy | infrastructure | Connect the GitHub repo; configure auto-deploy on push to `main`; protect non-`main` branches from auto-deploy | F1.1 |
| F1.5 | `/healthz` verification on Railway | infrastructure | Confirm the existing health probe works through Railway's proxy (HEAD + GET) | F1.1, F1.2 |
| F1.6 | Smoke-deploy current `main.py` | infrastructure | Deploy the existing CSV-based app to verify the full Railway path (build → run → respond) before any code changes | F1.1–F1.5 |
| F2.1 | Postgres schema | core | Define tables for `vouchers`, `customers`, `stations`, `presets`, `prices`, `discounts`, `audit_log` matching the current `VOUCHER_COLUMNS` shape plus sidecar-store content | None (can be designed before F2.2) |
| F2.2 | Complete `PostgresRepo` | core | Implement the full `Repo` interface (`list_recent_vouchers`, `list_all_vouchers`, `get_voucher`, `set_status`, `append_vouchers`, `update_voucher_fields`, `create_unverified_booking`); add a connection pool + transaction handling | F2.1 |
| F2.3 | Replace JSON sidecar stores with Postgres-backed equivalents | core | `price_store.py` and `discount_store.py` become thin wrappers over Postgres tables; preserve the public function signatures so call sites don't change | F2.1, F2.2 |
| F2.4 | Move ops audit log to Postgres | core | Replace the `ops_audit_log.csv` write path with an insert into the new `audit_log` table; preserve the existing column shape and append-only semantics | F2.1, F2.2 |
| F2.5 | Data migration script | core | Idempotent script: reads all CSVs and JSON files from a snapshot, validates row counts and key invariants, inserts into Postgres, writes a verification report. Re-runnable. | F2.1, F2.2, F2.3, F2.4 |
| F2.6 | File asset pipeline on Volume | core | Update `generate_voucher.py` and `report_pdf.py` to write under the Volume mount path; audit every `open(..., "w")` call in the codebase and ensure none writes to ephemeral disk | F1.1, F2.2 |
| F3.1 | Mandatory env-driven secrets | cross-cutting | Remove the hardcoded defaults for `ADMIN_KEY`, `SUPPLIER_API_TOKEN`, `secret_key`; add a `_require_production_env()` gate that refuses to boot when running under `gunicorn` if any are missing | None |
| F3.2 | Basic test suite + CI gate | cross-cutting | `pytest` covering: (a) pricing math (snapshot + freeze), (b) voucher lifecycle transitions including `ENFORCE_PHASES`, (c) `PostgresRepo` round-trip against a throwaway DB. Run on every push via a minimal CI step. | F2.2 |
| F3.3 | Resolve `/discount-locator` | cross-cutting | Either implement the missing `templates/locator.html` (matching the route's contract) or remove the route. Decision documented. | None |
| F3.4 | Dedupe helper functions | cross-cutting | Consolidate `_norm_dashes` / `_slug` (and any other duplications found) into a single `utils/text.py` module; update all call sites | None |
| F3.5 | CSRF protection on form endpoints | cross-cutting | Enable Flask-WTF (or hand-rolled CSRF tokens) on `/book`, `/register`, `/admin/*`, `/ops/...` POST handlers; exempt the JSON API and the supplier token-gated endpoint | F3.1 |
| F3.6 | Structured logging | cross-cutting | Replace ad-hoc prints / strings with Python `logging`; include request ID, route, IP, user-agent in every log line; wire to stdout for Railway log capture | None |
| F3.7 | Phase ordering on by default | core | Flip `ENFORCE_PHASES` to default-on; document the override; verify the `Redeemed` → `Unredeemed` and other illegal transitions are blocked in the test suite | None |
| F4.1 | Pre-cutover snapshot | infrastructure | Freeze writes on Replit; snapshot all CSVs, JSON files, generated assets, and customer data; store the snapshot in a safe location before DNS cutover | F2.5 complete, F2.6 complete |
| F4.2 | DNS cutover | infrastructure | Update `unifleet.asia` DNS to point at Railway; verify TLS; verify hostname resolution from multiple regions | F4.1, F3.* complete |
| F4.3 | Production smoke tests | core | Run end-to-end flows on production: book → approve → redeem, supplier API call, admin price update, public JSON API. Verify against `APP_REPORT.md` route inventory. | F4.2 |
| F4.4 | Deployment playbook | cross-cutting | Document: how to deploy (push to `main`), how to roll back (Railway revert), how to inspect logs, how to restore the DB from backup, how to scale the service, how to rotate secrets | F1.4, F3.* complete |
| F4.5 | Local dev setup docs | cross-cutting | Document: Poetry install, `.env.example` usage, how to bring up Postgres locally, how to run the data migration against a local DB | F2.5, F3.1 complete |
| F4.6 | Architecture / schema reference | cross-cutting | Living doc describing the Postgres schema, the Volume layout, the route inventory, the env-var catalog, and the data flow | F2.1, F2.6 complete |
| F4.7 | Rewrite `README.md` | cross-cutting | Replace the one-line README with project overview, quickstart, link to the playbook, link to the schema reference | F4.4, F4.5, F4.6 |
| F4.8 | Decommission Replit | infrastructure | After the cutover has been stable for one week, take the Replit service offline; archive (do not delete) the project for 30 days | F4.3, F4.4 |

### Feature Dependencies

```
F1.1 (Railway + Postgres + Volume)
├── F1.2 (Domain)
├── F1.3 (Env vars)
├── F1.4 (CI/CD)
├── F1.5 (Healthz)
├── F1.6 (Smoke deploy) ── depends on F1.2, F1.3, F1.4, F1.5
└── F2.6 (Volume file pipeline) ── depends on F1.1

F2.1 (Schema)
├── F2.2 (PostgresRepo)
│   ├── F2.3 (Sidecar stores)
│   ├── F2.4 (Audit log)
│   └── F2.5 (Migration script) ── depends on F2.3, F2.4
└── F2.5 (also depends on F2.1)

F3.1 (Secrets) ── independent, but should land before F3.5
F3.2 (Tests) ── depends on F2.2 (needs the repo to test)
F3.3 (locator) ── independent
F3.4 (Helpers) ── independent
F3.5 (CSRF) ── depends on F3.1
F3.6 (Logging) ── independent
F3.7 (Phase ordering) ── independent

F4.1 (Snapshot) ── depends on F2.5, F2.6
F4.2 (DNS cutover) ── depends on F4.1, F3.*
F4.3 (Smoke tests) ── depends on F4.2
F4.4 (Playbook) ── depends on F1.4, F3.*
F4.5 (Dev docs) ── depends on F2.5, F3.1
F4.6 (Schema ref) ── depends on F2.1, F2.6
F4.7 (README) ── depends on F4.4, F4.5, F4.6
F4.8 (Decommission) ── depends on F4.3, F4.4
```

### Cross-Cutting Concerns

- **Secrets & boot-time safety** — affects every endpoint; strategy: a single `_require_production_env()` gate at app construction time, called when `gunicorn` is the runner.
- **Ephemeral filesystem** — affects every write path; strategy: F2.6 audits and rewrites; F4.4 documents the Volume mount points; a test asserts no runtime path under `/tmp` is referenced from a non-test code path.
- **Timezone handling** — affects every timestamp; strategy: standardize on `zoneinfo` and deprecate `pytz` usage in new code (don't rip out existing `pytz` calls; mark them for follow-up).
- **Backwards compatibility of voucher columns** — affects F2.1; strategy: keep the `*_php` legacy mirror columns for one cycle to avoid breaking any downstream CSV consumer, drop in a follow-up project.
- **Test coverage as a moving target** — affects F3.2; strategy: start with the three highest-value test categories (pricing, lifecycle, repo round-trip); additional tests land with the features that need them.

## Delivery Phases

### Phase 1: Infrastructure & Deployment Foundation
**Goal:** A blank (or near-blank) Flask app is live at `unifleet.asia` on Railway, connected to a real Postgres database, with a persistent Volume mounted, env-driven config, and a working deploy pipeline. The current CSV-based app is running on Railway before any code changes.
**Features:** F1.1, F1.2, F1.3, F1.4, F1.5, F1.6
**Risk:** DNS cutover timing if `unifleet.asia` already has traffic; Railway free-tier resource limits on the Postgres plan; missing nix packages (e.g., `freetype`, `glibcLocales` for Pillow + reportlab) need to be declared in the build.
**Definition of done:** A push to `main` produces a live deployment at `unifleet.asia` serving the current CSV-backed app, with `/healthz` returning 200 and a `psql` connection to the managed Postgres succeeding.

### Phase 2: Database Refactor (CSV → Postgres)
**Goal:** The app runs entirely off Postgres + Volume. CSV is fully retired from the request path. Historical data is migrated and verified.
**Features:** F2.1, F2.2, F2.3, F2.4, F2.5, F2.6
**Depends on:** Phase 1 complete (need the Postgres instance + Volume live before schema is meaningful)
**Risk:** Schema parity with the 28-column `VOUCHER_COLUMNS` is the most likely place for silent data loss; the migration script must report row counts and key invariants; `PostgresRepo` is the single largest code change in the project.
**Definition of done:** The data migration script reports zero diffs between source CSVs/JSON and target Postgres tables. A booking can be created in `Unverified`, approved to `Unredeemed`, redeemed, and the corresponding row exists in Postgres with the same shape as the legacy CSV row.

### Phase 3: Production Hardening & Cleanup
**Goal:** The app is safe to run as a production service. The rough edges identified in `APP_REPORT.md` are resolved. Real test coverage exists for the most important behaviors.
**Features:** F3.1, F3.2, F3.3, F3.4, F3.5, F3.6, F3.7
**Depends on:** Phase 2 complete (need the Postgres repo to write tests against)
**Risk:** F3.2 (tests) is unbounded if scoped as "test everything" — keep it strictly to the three categories. F3.5 (CSRF) can break form submissions if not applied correctly; needs careful per-route audit.
**Definition of done:** All hardcoded secret defaults are removed. The test suite is green and runs on every push. The `/discount-locator` route either renders correctly or has been removed. Helper deduplication is done. CSRF is enforced on every state-changing HTML form. `ENFORCE_PHASES=1` is the default.

### Phase 4: Cutover & Deployment Runbook
**Goal:** Live traffic flows to `unifleet.asia` on Railway. Another operator can deploy, roll back, and run the system without tribal knowledge. The Replit instance is retired.
**Features:** F4.1, F4.2, F4.3, F4.4, F4.5, F4.6, F4.7, F4.8
**Depends on:** Phases 1–3 complete
**Risk:** DNS TTL means the cutover is not instantaneous globally; lower the TTL a few days in advance. The smoke tests in F4.3 need to be written before the cutover, not after.
**Definition of done:** `unifleet.asia` resolves to Railway. All routes from the original `APP_REPORT.md` route inventory return their expected response. The deployment playbook, local dev docs, schema reference, and README are all in place. Replit is decommissioned after a one-week stability window.

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|------------------------|----------------|
| 1 | Host on Railway | Replit (status quo), Render, Fly, Cloud Run, AWS | User decision; aligns with managed Postgres, Volume, GitHub deploys |
| 2 | Use Postgres (not SQLite) | Finish existing `DBRepo` (SQLite), MySQL | User decision; production-grade, ACID, better for the eventual multi-table schema; also matches Railway's managed-DB story |
| 3 | File storage on Railway Volume | S3, Cloudflare R2, Backblaze B2, GCS | User decision (option i); simplest, single-instance is acceptable for current scale, defers object-store complexity |
| 4 | CI/CD via Railway GitHub integration | GitHub Actions → Railway API, manual deploys | User decision; least moving parts, push-to-deploy is sufficient for a solo operator |
| 5 | Cleanup scope = option C (full cleanup) | Pure lift-and-shift (A), minimum hardening (B) | User decision; uses the migration as the forcing function to retire known rough edges |
| 6 | Real auth is out of scope | Sessions, login, RBAC | Confirmed as a follow-up project; the existing key-gates remain |
| 7 | 4 phases (no split of Phase 2 or 3) | Split DB refactor behind a feature flag, split cleanup into 3a/3b | User decision; pragmatic for the actual scope and team size |
| 8 | Keep `*_php` legacy mirror columns in Phase 2 | Drop them now, drop them after one release cycle | Avoids breaking any downstream CSV consumer during the migration; cleanup is a separate ticket |
| 9 | Standardize on `zoneinfo` for new code; deprecate `pytz` lazily | Rip out `pytz` in Phase 3, add `pytz` everywhere | Matches the direction the code is already going; avoids a risky global replace |
| 10 | `/discount-locator` decision deferred to F3.3 | Implement now, remove now | Small surface area; no callers depend on it; resolution happens during cleanup |

## Open Questions

- **Is there any production traffic on `unifleet.asia` today?**
  - **Impact if unresolved:** F1.2 (domain binding) and F4.2 (DNS cutover) cannot estimate their blast radius; the snapshot + cutover plan in Phase 4 has unknown error cost.
  - **Suggested default:** Plan as if there is real traffic (lower DNS TTL pre-cutover, schedule the cutover for a low-traffic window, keep Replit warm for one week as rollback target).
- **What's the existing `BASE_URL` value in production?** (Currently hardcoded in `generate_voucher.py`.)
  - **Impact if unresolved:** Generated QR codes will point to the wrong domain until corrected; the existing vouchers in the system may have stale URLs.
  - **Suggested default:** Treat it as a mandatory env var, capture the production value as part of the Replit snapshot, and have the data migration include a verification step.
- **Postgres plan tier on Railway?**
  - **Impact if unresolved:** Volume size, connection limits, and cost depend on this.
  - **Suggested default:** Start on the Hobby plan; reassess after one month of real usage data.
- **What backup strategy for the Postgres DB?**
  - **Impact if unresolved:** A bad migration run or accidental `DELETE` is unrecoverable.
  - **Suggested default:** Rely on Railway's automated daily backups for the Hobby plan; document a manual `pg_dump` schedule as a follow-up enhancement.

## Next Steps

The following features each need their own `plan-feature` session. Start with Phase 1's infrastructure features (they have no internal dependencies and unblock everything else).

1. **F1.1** — Railway project, service, Postgres, Volume provisioning. (Start here.)
2. **F1.2** — Domain binding for `unifleet.asia`.
3. **F1.3** — Env-var management + `.env.example`.
4. **F1.4** — Railway GitHub deploy integration.
5. **F1.5** — `/healthz` verification on Railway.
6. **F1.6** — Smoke-deploy current `main.py` to Railway.
7. **F2.1** — Postgres schema design.
8. **F2.2** — Complete `PostgresRepo`.
9. **F2.3** — Replace JSON sidecar stores with Postgres-backed equivalents.
10. **F2.4** — Move ops audit log to Postgres.
11. **F2.5** — Data migration script.
12. **F2.6** — File asset pipeline on the Volume.
13. **F3.1** — Mandatory env-driven secrets.
14. **F3.2** — Basic test suite + CI gate.
15. **F3.3** — Resolve `/discount-locator`.
16. **F3.4** — Dedupe helper functions.
17. **F3.5** — CSRF protection on form endpoints.
18. **F3.6** — Structured logging.
19. **F3.7** — Phase ordering on by default.
20. **F4.1** — Pre-cutover snapshot.
21. **F4.2** — DNS cutover.
22. **F4.3** — Production smoke tests.
23. **F4.4** — Deployment playbook.
24. **F4.5** — Local dev setup docs.
25. **F4.6** — Architecture / schema reference.
26. **F4.7** — Rewrite `README.md`.
27. **F4.8** — Decommission Replit.

Start with: `/plan-feature for: F1.1 — Railway service + Postgres + Volume provisioning (from PROJECT-migrate-to-railway.md)`

---
_This project plan is the input for individual plan-feature sessions._
_Each feature listed above should be planned separately before task generation._
