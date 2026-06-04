# Plan: F1.1 — Railway service + Postgres + Volume provisioning

> **Date:** 2026-06-04
> **Project source:** `specs/plans/PROJECT-migrate-to-railway.md`
> **Estimated tasks:** ~12
> **Planning session:** detailed

## Summary

Provision a new Railway project (`unifleet`) with a single `web` service, a managed Postgres database (`unifleet`), and a persistent Volume (`data`) mounted at `/data`. Add two new repo files — a one-route Flask "sanity app" and a one-time build-probe script — to prove the build pipeline works end-to-end (Poetry, Pillow, reportlab, psycopg 3) and that the Volume is actually persistent. No changes to the existing `main.py` and no cutover of production traffic; this feature is "platform exists and works," nothing more.

## Requirements

### Functional Requirements
1. A Railway project named `unifleet` exists with a `web` service, a `unifleet` Postgres database, and a `data` Volume mounted at `/data`.
2. The Railway service is configured to build from this GitHub repository on push to `main` and to start via gunicorn, binding to `$PORT`.
3. A one-route Flask sanity app is the deployable entry point of the `web` service for the duration of F1.1.
4. A build-probe script imports every heavy native dependency and `psycopg`, attempts a `SELECT 1` against the live Postgres, and exits non-zero on any failure.
5. A documented one-time manual test confirms that a file written to `/data` survives a redeploy of the service.
6. A documented manual test confirms that `psql $DATABASE_URL -c "SELECT 1"` succeeds from a Railway shell.
7. No code in `main.py` is changed, and no real application route (`/form`, `/book`, `/redeem`, etc.) is reachable until F1.6.

### Non-Functional Requirements
1. The service responds 200 on the sanity app's `/` route within 5 seconds of deploy completion.
2. The build completes without relying on the Replit-specific `.replit` file; nix packages required by Pillow/reportlab are declared in a Railway-native way.
3. All secret material (the future `ADMIN_KEY`, `SUPPLIER_API_TOKEN`, `secret_key`, `DATABASE_URL`) is held in Railway env vars, never in the repo.
4. The build probe is idempotent — running it multiple times produces the same result and has no destructive side effects.

## Behaviors

### Sanity app

**Why rules matter:**
- F1.1's only job is to prove the platform works. A real route from `main.py` would conflate platform health with application health, and any failure during F1.1 would be ambiguous.
- Keeping the sanity app independent of `main.py` means F1.6 can replace it with a one-line config change (not a refactor).

**What's optional vs required:**
- Required: responds 200 on at least one route; uses the gunicorn-compatible `app` symbol.
- Optional: the route can return a static string, JSON, or a minimal environment dump. It should not read from disk, connect to Postgres, or import Pillow/reportlab.

**Common mistakes:**
- Forgetting to bind to `0.0.0.0:$PORT` (Railway's proxy expects this; binding to `127.0.0.1` or hardcoding port 5000 makes the service unreachable from the public URL).
- Accidentally importing from `main.py` (e.g., to share helpers), which pulls in the entire CSV/JSON sidecar stack and creates a misleading dependency surface for F1.6.
- Setting `debug=True`, which is inappropriate for a gunicorn-served environment and can leak the Werkzeug debugger.

### Build probe

**Why rules matter:**
- The build environment on Railway is not identical to the developer's laptop. Catching missing native dependencies (Pillow's freetype, reportlab's libfontconfig, etc.) at F1.1 prevents a cascade of confusing errors in F2.x and F3.x.
- "Hard fail on any missing dep" is the right posture for a one-time probe — false positives are cheap (re-run), false negatives are expensive (silent breakage later).

**What's optional vs required:**
- Required: importable check for Flask, Pillow, reportlab, qrcode, pandas, pytz, psycopg.
- Required: a Postgres `SELECT 1` round-trip using `$DATABASE_URL`.
- Required: a clear exit code (0 on success, non-zero on any failure) and a per-check pass/fail line in the output.
- Optional: human-readable summary at the end; JSON output for later CI consumption; pretty colors.

**Common mistakes:**
- Running the probe locally instead of on Railway (defeats the purpose — the whole point is to validate the Railway build environment).
- Catching `ImportError` too broadly and reporting "OK" anyway.
- Connecting to Postgres with the wrong driver; mixing `psycopg2` and `psycopg` connections in the same process.
- Hanging on a slow Postgres connection (no timeout).

### Railway resource layout

**Why rules matter:**
- Resource names show up in the dashboard, in `railway.toml`, in env-var references, and in operational runbooks. Changing them later requires updating all four.
- The Volume mount path (`/data`) is the foundation of the data layout for the entire migration; once written into the codebase, changing it is a search-and-replace across every write path in `main.py` and the sidecar stores.

**What's optional vs required:**
- Required: project name, service name, database name, volume name, volume mount path are all locked.
- Optional: per-resource regions (Hobby plan limits choices); resource tags; resource descriptions.

**Common mistakes:**
- Provisioning the Volume at a different mount path than the one assumed by the sanity app's test (the persistence test catches this — don't skip it).
- Provisioning Postgres in a different region than the service (cross-region DB calls are slow).
- Skipping the `asia-southeast` region in favor of the default, which on Railway is typically `us-west`.

## Detailed Specifications

### Railway `web` service

**Purpose:** Runs the sanity app for the duration of F1.1; will be re-pointed at `main:app` in F1.6.

**Interface:**
- Exposes a single public HTTP route: `GET /` → 200 with a short text or JSON body indicating the service is alive.
- Health-probe compatible: HEAD `/` also returns 200.
- Logs to stdout (Railway log capture requirement).

**Behavior:**
- On startup, binds to `0.0.0.0:$PORT` (Railway sets `$PORT`; the start command must reference it).
- Handles concurrent requests via gunicorn's default worker model (worker count and class are F1.6's decision; F1.1 uses 1 sync worker).
- Does not read from `/data`, does not connect to Postgres, does not import any heavy native dependency.

**Validation Rules:**
- `PORT` env var: required, integer, default 5000 only for local dev.
- No required env vars at the application level for F1.1 (the sanity app is dependency-free).

**Error Scenarios:**
| Condition | Expected Behavior |
|-----------|-------------------|
| `PORT` not set | Start command falls back to a sensible default or fails the boot with a clear error |
| Invalid route hit (e.g., `/form`) | 404 (the sanity app only defines `/`) |
| Process receives SIGTERM | Graceful shutdown via gunicorn's default handler |

### `scripts/verify_build.py`

**Purpose:** One-time build environment probe; runs on Railway via `railway run` (or Railway shell) and exits non-zero if any required dependency is missing or the Postgres connection fails.

**Interface:**
- CLI: `python scripts/verify_build.py`
- Exits 0 on full success, non-zero on any failure.
- Writes a structured pass/fail line per check to stdout.

**Behavior:**
- Imports Flask, Pillow, reportlab, qrcode, pandas, pytz, psycopg in sequence, recording the result of each.
- Reads `DATABASE_URL` from the environment; if missing, marks the DB check as "skipped — DATABASE_URL not set."
- Connects to Postgres with a 5-second connection timeout, runs `SELECT 1`, and records success/failure.
- Exits 0 only if every check passed (or the DB check was explicitly skipped because `$DATABASE_URL` is unset AND F1.1's environment is set up so it's set).
- Prints a final summary line (e.g., `RESULT: PASS` or `RESULT: FAIL — Pillow import`).

**Validation Rules:**
- Each import attempt: wrapped in try/except for `ImportError` (and `Exception` as a safety net for psycopg's optional-binary-load errors).
- DB connection: must use `psycopg` (the locked driver), not `psycopg2` or `pg8000`.
- DB connection timeout: 5 seconds; configurable via env var if a slower link is ever needed.

**Error Scenarios:**
| Condition | Expected Behavior |
|-----------|-------------------|
| Any required dependency missing | Print `FAIL: <dep>` line; exit 1 |
| `DATABASE_URL` unset | Print `SKIP: db`; exit 0 (F1.1 is structured to always have it set, but this allows the probe to be re-run safely) |
| DB connection times out | Print `FAIL: db (timeout)`; exit 1 |
| DB connection refused | Print `FAIL: db (refused)`; exit 1 |
| All checks pass | Print `RESULT: PASS`; exit 0 |

### Railway project resources

**Purpose:** Lock the resource topology that the rest of the migration depends on.

**Behavior:**
- Project `unifleet` contains: 1 service (`web`), 1 database (`unifleet`), 1 volume (`data`).
- Service `web` is connected to the GitHub repo, with auto-deploy on push to `main`.
- Database `unifleet` is provisioned in `asia-southeast` (Singapore); managed Postgres; exposes `DATABASE_URL` to the `web` service automatically.
- Volume `data` is attached to the `web` service, mounted at `/data`. Size: 8 GB (Hobby default; sufficient for years of generated voucher PNGs/PDFs at current scale).

**Validation Rules:**
- All four resources must exist in the Railway dashboard at the end of F1.1.
- The Volume must show as "Attached" to the `web` service, with mount path `/data`.
- The Postgres database must show as "Available" in the dashboard.

**Error Scenarios:**
| Condition | Expected Behavior |
|-----------|-------------------|
| `asia-southeast` not available in the current Railway account | Fall back to the next-closest region; document the deviation |
| Project name `unifleet` already taken on this account (e.g., a stale staging project) | Use a `uni-fleet` or account-prefixed variant; document the deviation |
| Volume size needs to exceed 8 GB | Bump the plan or accept partial; document the constraint |

### Volume persistence test (one-time, manual)

**Purpose:** Prove the Volume is actually persistent and not silently writing to ephemeral disk.

**Behavior:**
- Write a uniquely-named marker file to `/data` from a Railway shell (e.g., `printf "f1.1-persistence-test-$(date +%s)" > /data/persistence_marker.txt`).
- Trigger a redeploy of the `web` service (push a no-op commit, or use the Railway dashboard "Redeploy" button).
- After the redeploy completes, read the marker file from a fresh Railway shell: `cat /data/persistence_marker.txt`.
- Confirm the original timestamp string is intact.
- Record the result (success/failure, the marker content, the redeploy ID) in the F1.1 commit message or a follow-up note.

**Validation Rules:**
- The marker file's content must match exactly between write and read.
- The redeploy must be a full service restart (not just a config reload) to truly exercise persistence.

**Error Scenarios:**
| Condition | Expected Behavior |
|-----------|-------------------|
| Marker file missing after redeploy | The mount is wrong or not persistent; do not mark F1.1 done. Diagnose by re-checking the Volume's mount path in the Railway dashboard. |
| Marker file content is empty or truncated | The filesystem layer is not what was expected; do not mark F1.1 done. |

## Key Constraints

| Constraint | Why It Matters |
|------------|----------------|
| Volume mount path must be `/data` | Matches the existing codebase's `./data` convention; changing it later means rewriting every disk-write call site. Locked in F1.1, permanent. |
| Postgres driver must be `psycopg[binary]` (psycopg 3) | The build probe uses it; F2.2's `PostgresRepo` will use it; mixing drivers in the same process causes connection-pool conflicts. |
| `railway.toml` must declare the nix packages Pillow/reportlab need (`freetype`, `glibcLocales`) | Railway does not read `.replit`; without the explicit declaration, the build succeeds but runtime imports of Pillow/reportlab fail with obscure errors. |
| Start command must bind to `0.0.0.0:$PORT` | Railway's proxy expects this; the default `gunicorn` invocation listens on a Unix socket. A wrong bind means the deploy appears healthy in the dashboard but the public URL returns 502. |
| The sanity app must not import anything from the real `main.py` | Coupling the two defeats the purpose of F1.1; F1.6 needs to swap them with a config-only change. |
| Build probe must run on Railway, not locally | The whole point is to validate the Railway build environment; running it locally tests the developer's machine instead. |
| The real `main.py` is not deployed or even referenced until F1.6 | F1.1 is platform-only; pulling in the real app would conflate platform health with application health and obscure failures. |

## Edge Cases & Failure Modes

| Scenario | Decision | Rationale |
|----------|----------|-----------|
| First build fails because Poetry can't resolve the lock file | Run `poetry lock --no-update` locally and commit the updated `poetry.lock`; redeploy. | Railway builds use the lock file as the source of truth; a stale lock causes resolution failures. |
| Pillow import fails on Railway despite working locally | The nix package list is missing `freetype`. Add it to `railway.toml`'s `nixPackages` and redeploy. | Pillow's text rendering depends on freetype; without it, even `from PIL import Image` may fail at module load. |
| Postgres connection from the build probe times out at exactly 5 seconds | The probe reports `FAIL: db (timeout)`; do not interpret this as a config issue. Investigate region mismatch (service and DB in different regions) or a stalled Postgres instance. | A 5-second timeout is a sharp signal; if it's hit, something is structurally wrong, not transiently slow. |
| Volume mount path is `/data` in the dashboard but the service writes to `/mnt/data` in the build image | The persistence test catches this; if it fails, fix the dashboard's mount path, not the service code. | The service code's write path is the source of truth for F2.6; the dashboard must match it. |
| A second engineer clones the repo and runs the sanity app locally | The sanity app should start on `localhost:5000` with no env vars set, so local dev is unblocked. | F1.1 shouldn't introduce new local-dev friction. |
| `railway run` is unavailable (e.g., the local Railway CLI isn't installed) | Fall back to opening a Railway shell from the dashboard and running the same command. | The script's contract is "run it on Railway," not "run it via a specific CLI." |
| The first deploy succeeds but the service URL returns 502 | The start command is binding to the wrong host/port. Check the gunicorn invocation in `railway.toml`. | The dashboard will show "Deployed" but the proxy can't reach the worker. |
| `DATABASE_URL` is auto-injected by Railway but uses a `postgres://` URL that psycopg 3 rejects | Use `psycopg.conninfo.conninfo_to_dict` or pass it through `psycopg.connect()` which accepts both `postgres://` and `postgresql://`. No code change required. | psycopg 3 handles both schemes natively; this is defensive, not a known failure. |
| The Volume is provisioned in a different region than the service | Railway permits this but warns about latency. Acceptable for F1.1 if the warning is documented; revisit before F4.2 (cutover) if it causes user-visible latency. | F1.1's traffic is internal (the build probe), so latency is not yet user-facing. |

## Decisions Log

| # | Decision | Alternatives Considered | Chosen Because |
|---|----------|------------------------|----------------|
| 1 | Use a separate `sanity_app.py` file (deleted in F1.6) | Env-flagged sanity mode in `main.py`; overwrite `main.py` with a minimal version | Cleanest separation; F1.6 swap is a one-line config change |
| 2 | Use a one-time `scripts/verify_build.py` invoked via `railway run` | Release command in `railway.toml`; inline route in the sanity app | Honors the "one-time" intent; doesn't pollute the deploy config long-term |
| 3 | Build probe hard-fails on any missing dep | Soft report (always exit 0); tiered (critical vs optional) | A one-time probe should be loud; false positives are cheap to re-run |
| 4 | Sanity app does NOT touch Postgres; build probe does | Sanity app has a `/dbcheck` route; `/healthz` reflects DB status | Decouples platform health from DB health; the build probe is the right place for an explicit one-time DB check |
| 5 | Volume mount path = `/data` | `/mnt/unifleet-data`; `/unifleet/data` | Matches existing `./data` convention in the codebase; minimum F2.6 churn |
| 6 | Volume persistence verified by write→redeploy→read, once | Self-checking `/healthz` route; trust Railway docs | Sufficient for F1.1, doesn't add complexity to a soon-to-be-deleted app, F2.6 will exercise the Volume heavily |
| 7 | Hobby plan, `asia-southeast` region | Higher tier; US regions | Lowest cost that's still production-viable for current scale; Singapore is the closest available region to the Philippines |
| 8 | Resource names: project `unifleet`, service `web`, DB `unifleet`, volume `data` | Various alternatives | Short, conventional, easy to reference in `railway.toml` and operational docs |
| 9 | Postgres driver: `psycopg[binary]` (psycopg 3) | `psycopg2-binary`; `pg8000` | Modern default from the `psycopg` maintainers; prebuilt wheels; better connection-pool API for F2.2 |
| 10 | Boundary: F1.1 = resources + sanity app + build probe; F1.6 = deploy real `main.py` | Bundle into one feature | Keeps the F1.1 "platform exists and works" milestone independent of application concerns |

## Scope Boundaries

### In Scope
- Provisioning the Railway project, service, Postgres DB, and Volume
- Configuring the build (Poetry) and start (gunicorn) commands in `railway.toml`
- Declaring nix packages needed by Pillow/reportlab
- Adding `psycopg[binary]` to `pyproject.toml` as a runtime dependency
- Writing `sanity_app.py` (minimal, dependency-free)
- Writing `scripts/verify_build.py` (one-time build probe)
- Connecting the GitHub repo to Railway for auto-deploy
- Running the build probe, Volume persistence test, and Postgres connectivity test
- Documenting the result of each in the F1.1 commit message or follow-up

### Out of Scope
- Domain binding for `unifleet.asia` (F1.2)
- Full env-var catalog and `.env.example` (F1.3) — only `DATABASE_URL` and `PORT` are needed for F1.1
- Any code changes to `main.py`
- Any real application route being reachable
- Production traffic cutover from Replit
- Real authentication, CSRF, or hardening of any kind
- Test suite for the application (F3.2)
- Backup or DR strategy for the Volume or DB (F4.4)

## Dependencies

### Depends On (must exist before this work starts)
- A Railway account with permission to create projects in the `unifleet` name (or a documented alternative)
- The `psycopg[binary]` package being installable on the Railway build platform for Python 3.11 (verify via the build probe)
- A GitHub repository the developer can grant Railway access to

### Depended On By (other work waiting for this)
- **F1.2 (Domain binding)** — needs the service's Railway-provided URL to point `unifleet.asia` at
- **F1.3 (Env-var management)** — needs the deployed service to expand the env-var catalog against
- **F1.4 (CI/CD verification)** — needs at least one successful Railway deploy to validate the GitHub integration
- **F1.5 (`/healthz` verification)** — needs the `web` service deployed and reachable
- **F1.6 (Smoke deploy)** — needs the deploy pipeline proven to work end-to-end with the sanity app
- **F2.1 (Postgres schema)** — needs the Postgres DB live and connectable
- **F2.2 (`PostgresRepo`)** — needs the same Postgres DB plus the `psycopg` driver dependency
- **F2.6 (File asset pipeline on Volume)** — needs the Volume mounted at `/data` and persistence verified
- **F3.2 (Test suite)** — needs the build environment proven to support the test runner
- **F4.4 (Deployment playbook)** — needs the deploy process to be repeatable and documented in real terms

## Architecture Notes

- The `web` service's identity in Railway persists across F1.1 → F1.6 and beyond. Once committed, the service name, build configuration, and start command should not change without a deprecation period.
- The Postgres database is the foundation for every feature in Phase 2 (DB refactor) and Phase 3 (cleanup). Its region and plan tier are load-bearing decisions for the entire migration; a region change after F2.x ships requires a data migration.
- The Volume is the foundation for F2.6 (file asset pipeline). The mount path `/data` is referenced by every code path that writes files; the persistence test in F1.1 is the only thing that proves the configuration is correct.
- The `psycopg[binary]` driver choice is committed at F1.1 (it's added to `pyproject.toml` here) and cannot be changed without a full code audit of every connection-pooling site. This is intentional — picking the driver up front is cheaper than picking it under time pressure in F2.2.
- The sanity app exists for one reason: to prove the platform works. It is not a template for the real app and should not be extended. Any temptation to add "just one more route" to the sanity app is a sign the feature is bleeding into F1.6.
- The build probe script is reusable in later phases. Promote it to a release command or CI check in a follow-up if its value persists; do not rewrite it during F1.1.

## Open Questions

- **Is the GitHub repo currently private, and does the developer have admin access to grant Railway the integration?**
  - **Impact if unresolved:** F1.1 cannot complete auto-deploy configuration; manual deploys from the Railway dashboard become the fallback for F1.1 and beyond.
  - **Suggested default:** Assume yes; if not, the build command can be triggered manually via the Railway CLI from a developer's machine that does have repo access.
- **Does the Railway account already have a project named `unifleet` (e.g., a stale staging project)?**
  - **Impact if unresolved:** Project name collision blocks provisioning; F1.1 stalls at the first step.
  - **Suggested default:** Use the Railway CLI to list existing projects first; rename or delete the collision before provisioning.
- **Should the build probe's output format be JSON (for later CI consumption) or plain text?**
  - **Impact if unresolved:** Cosmetic only; doesn't affect F1.1's correctness.
  - **Suggested default:** Plain text with `RESULT: PASS` / `RESULT: FAIL` as the final line — easy to grep, easy to read in shell scrollback. JSON is a follow-up.

---
_This plan is the input for the generate-tasks skill._
_Review this document, then run: "Generate task from plan: specs/plans/PLAN-railway-provisioning.md"_

---

# Tasks

## Task T1: Local code + config (railway.toml, sanity_app, verify_build, deps)

> **Status:** done
> **Effort:** m
> **Priority:** critical
> **Depends on:** None

### Description

Add the repo-side artifacts the Railway deploy needs: a `railway.toml` that declares the build/start commands and the nix packages Pillow + reportlab require, a minimal `sanity_app.py` Flask entry point, a one-time `scripts/verify_build.py` that imports every heavy native dependency and exercises a `SELECT 1` against Postgres, the `psycopg[binary]` runtime dependency, the `pytest` dev dependency, and unit tests covering all three. T1 is fully testable on a developer laptop with mocked dependencies; it produces a green test suite and a deployable codebase without touching Railway.

### Test Plan

#### Test File(s)
- `tests/test_sanity_app.py`
- `tests/test_verify_build.py`
- `tests/test_railway_toml.py`
- `tests/conftest.py` (only if shared fixtures are needed for mocking)

#### Test Scenarios

##### Sanity app: HTTP contract

- **`test_root_returns_200_on_get`** — GIVEN the sanity app is running WHEN a GET `/` is made via Flask's test client THEN the response status is 200 AND the response body is non-empty.
- **`test_root_returns_200_on_head`** — GIVEN the sanity app is running WHEN a HEAD `/` is made via Flask's test client THEN the response status is 200. (Mirrors the existing `/healthz` contract at `main.py:38-50`; Railway's proxy may send HEAD.)
- **`test_app_symbol_is_a_flask_app`** — GIVEN the module is imported WHEN `from sanity_app import app` THEN `app` is an instance of `flask.Flask` AND it is gunicorn-servable.

##### Sanity app: isolation

- **`test_does_not_import_main`** — GIVEN the sanity app source WHEN its top-level imports are inspected (by reading the source file and parsing `import` / `from … import` statements) THEN none of them reference `main` (directly or via a parent package). Structural check that protects F1.6's clean swap.
- **`test_does_not_import_heavy_native_deps`** — GIVEN the sanity app source WHEN its top-level imports are inspected THEN it must not import `PIL`, `reportlab`, `pandas`, `qrcode`, or `psycopg`. Keeps the sanity app dependency-free and fast to boot.

##### Build probe: happy path

- **`test_passes_when_all_deps_and_db_are_present`** — GIVEN every required dep imports successfully (mocked) AND `$DATABASE_URL` is set in the test env AND a mocked `psycopg.connect()` returns a context manager that yields a cursor whose `execute("SELECT 1")` returns `1` WHEN the probe is invoked THEN exit code is 0 AND stdout contains the literal line `RESULT: PASS`.
- **`test_enumerates_all_required_deps`** — GIVEN the probe runs WHEN its per-dep output is captured THEN the reported dep names include (in any order): `Flask`, `Pillow`, `reportlab`, `qrcode`, `pandas`, `pytz`, `psycopg`. Adding a new required dep must require updating this test.

##### Build probe: failure paths

- **`test_fails_when_a_dep_is_missing`** — GIVEN `Pillow` import raises `ImportError` (mocked) WHEN the probe runs THEN exit code is non-zero AND stdout contains `FAIL: Pillow` AND stdout does NOT contain `RESULT: PASS`.
- **`test_fails_when_db_connection_fails`** — GIVEN all imports succeed AND `$DATABASE_URL` is set AND `psycopg.connect()` raises (mocked) WHEN the probe runs THEN exit code is non-zero AND stdout contains `FAIL: db`.

##### Build probe: defensive paths

- **`test_skips_db_when_database_url_unset`** — GIVEN `$DATABASE_URL` is unset in the test env WHEN the probe runs THEN the DB check is reported as `SKIP` (not `PASS`, not `FAIL`) AND exit code is 0. This allows the probe to be run safely before the env var is wired up.
- **`test_db_connection_uses_five_second_timeout`** — GIVEN the probe runs WHEN it calls `psycopg.connect()` THEN the `connect_timeout` kwarg is 5 (matches the plan's "5-second connection timeout" decision).

##### Railway config

- **`test_railway_toml_exists_and_parses`** — GIVEN the file `railway.toml` is at the repo root WHEN it is read with Python's `tomllib` THEN parsing succeeds AND the resulting dict is non-empty.
- **`test_railway_toml_declares_poetry_build`** — GIVEN the parsed TOML WHEN the build command is read THEN it contains the substring `poetry install`.
- **`test_railway_toml_declares_gunicorn_start_with_dynamic_port`** — GIVEN the parsed TOML WHEN the start command is read THEN it contains `gunicorn` AND it contains the literal token `0.0.0.0:$PORT` (not a hardcoded port). The hardcoded `0.0.0.0:5000` from `.replit` line 1 must NOT appear.
- **`test_railway_toml_declares_required_nix_packages`** — GIVEN the parsed TOML WHEN the nix package list is read THEN it contains `freetype` AND `glibcLocales` (matches the `.replit` declaration at lines 8-9 that's being retired).

### Implementation Notes

- **Layer(s):** Infrastructure config (railway.toml), minimal application shim (sanity_app), one-time build probe (verify_build).
- **Pattern reference:** `main.py:38-50` for the HEAD-aware route pattern; `main.py:31` for the `app = Flask(__name__)` instance pattern; `main.py:97-98` for env-var-with-default pattern (verify_build should mirror this for `DATABASE_URL`).
- **Key decisions (from plan's Decisions Log):**
  - 1, 2, 3: separate `sanity_app.py` + one-time `scripts/verify_build.py` + hard-fail on missing dep
  - 4: sanity app does NOT touch Postgres; verify_build does
  - 5: Volume mount path = `/data` (verify_build should not write to `/data`; that's T2's manual test)
  - 9: Postgres driver = `psycopg[binary]` (psycopg 3) — the probe and all future code use this driver, NOT `psycopg2` or `pg8000`
  - 10: F1.1 boundary excludes any change to `main.py`; F1.6 will swap the entry point
- **Libraries:**
  - Add `psycopg[binary] = "^3.1"` to `[tool.poetry.dependencies]` (runtime)
  - Add `pytest = "^8.0"` to a new `[tool.poetry.group.dev.dependencies]` section (dev only)
  - Add `[tool.pytest.ini_options]` with `testpaths = ["tests"]` so `poetry run pytest` finds tests without flags
  - No other library changes; sanity_app uses Flask's stdlib `app.test_client()` for tests; verify_build mocks `psycopg` via `unittest.mock` (stdlib, no new dep)
- **Python version:** current constraint is `>=3.11.0,<3.12` in `pyproject.toml:8` — `tomllib` is in the stdlib for 3.11+, no extra dep needed for the TOML test.

### Scope Boundaries

- DO NOT deploy anything to Railway (T2's job).
- DO NOT modify `main.py` or any other existing source file.
- DO NOT modify the existing `.replit` file (T1 only adds `railway.toml` alongside it; cleanup is a follow-up).
- DO NOT add a `/dbcheck` route or any other Postgres-touching route to the sanity app.
- DO NOT add CSRF, authentication, structured logging, or any other hardening from F3.x.
- DO NOT add production-grade error handling to `verify_build.py` (one-line-per-check + `RESULT: PASS/FAIL` is sufficient per the plan's "Required" section).
- DO NOT add a database fixture or integration test that requires a real Postgres connection (the integration verification is T2's manual step, not T1's automated test).
- Only: add the three new code/config files, update `pyproject.toml` for the two new deps and pytest config, and write the unit tests.

### Files Expected

**New files:**
- `railway.toml` — build/start config + nix packages, per the plan's "Railway project resources" section.
- `sanity_app.py` — minimal Flask app exposing a single `/` route (GET and HEAD), no business logic, no imports from `main`, no imports of heavy native deps.
- `scripts/__init__.py` — empty marker so `scripts/` is importable as a package (required for the probe to be a proper module).
- `scripts/verify_build.py` — CLI entry point. Imports each required dep, attempts a `SELECT 1` against `$DATABASE_URL` (with 5-second timeout) using `psycopg`, prints a pass/fail line per check, prints a final `RESULT: PASS` or `RESULT: FAIL` line, exits 0/non-zero accordingly.
- `tests/__init__.py` — empty marker.
- `tests/test_sanity_app.py` — 5 tests from the test plan above.
- `tests/test_verify_build.py` — 8 tests from the test plan above.
- `tests/test_railway_toml.py` — 4 tests from the test plan above.

**Modified files:**
- `pyproject.toml` (reason: add `psycopg[binary]` to runtime deps, add `pytest` to a new dev-deps group, add `[tool.pytest.ini_options]` with `testpaths = ["tests"]`).

**Must NOT modify:**
- `main.py` (reason: F1.1 is platform-only; F1.6 swaps the entry point; any change here conflates the two).
- `persistence.py`, `models.py`, `price_store.py`, `discount_store.py`, `generate_voucher.py`, `report_pdf.py` (reason: not in F1.1 scope; the DB refactor lands in Phase 2).
- All `templates/*` and `static/*` (reason: not in F1.1 scope; no UI changes for the sanity app).
- `.replit` (reason: T1 only adds `railway.toml` alongside; `.replit` cleanup is a follow-up that depends on F1.2's domain decision).
- `data/*` (reason: T1 does not touch the data directory; T2's manual test writes the marker file to `/data` on Railway, but that file is not in the repo).

### TDD Sequence

1. **Red** — Write `tests/test_sanity_app.py` (5 tests). Run `poetry run pytest tests/test_sanity_app.py` and confirm it fails because `sanity_app` does not exist.
2. **Green** — Write the minimum `sanity_app.py` to pass the 5 sanity tests. Confirm green.
3. **Red** — Write `tests/test_verify_build.py` (8 tests). Run and confirm red because `scripts/verify_build.py` does not exist.
4. **Green** — Write the minimum `scripts/verify_build.py` to pass the 8 probe tests. Use `unittest.mock` to mock `psycopg.connect` and the imports. Confirm green.
5. **Red** — Write `tests/test_railway_toml.py` (4 tests). Run and confirm red because `railway.toml` does not exist.
6. **Green** — Write the minimum `railway.toml` to pass the 4 config tests. Confirm green.
7. **Update `pyproject.toml`** — Add `psycopg[binary]` to runtime deps, add `pytest` to a new dev-deps group, add `[tool.pytest.ini_options]`.
8. **Install + run all tests** — `poetry install` then `poetry run pytest`. Confirm all 17 tests are green.
9. **Smoke check locally** — Start the sanity app with `poetry run gunicorn --bind 127.0.0.1:5000 sanity_app:app` in a separate shell, `curl http://127.0.0.1:5000/`, confirm 200 with non-empty body, kill the server. (Manual, not in the test suite.)

## Task T2: Railway provisioning + on-Railway verifications

> **Status:** done (operational prep); on-Railway execution pending operator
> **Effort:** s
> **Priority:** critical
> **Depends on:** T1

### Description

Provision the Railway resources (project `unifleet`, service `web`, Postgres `unifleet`, Volume `data` mounted at `/data`), connect the GitHub repo for auto-deploy, and execute the three on-Railway verifications called out in the plan: the sanity app responds, the build probe passes, and the Volume is actually persistent. This task is operational, not code-bearing; its "tests" are manual verifications documented in the task's commit message or a follow-up note.

### Prep work completed (2026-06-04)

The repo-side and tooling-side prep is done. The on-Railway execution requires a logged-in operator and is pending.

**Repo state:**
- T1 commit (`902c2dd feat(infra): F1.1 — Railway deploy scaffold`) is on `origin/main` and will be the first deploy when the GitHub integration is linked.
- Working branch `dev` is one commit ahead of nothing (fast-forwarded to main).

**New files for the operator:**
- `scripts/provision_railway.sh` — idempotently creates the project, service, Postgres, and Volume. **Stops and asks on `unifleet` project-name collision** (per the user's T2 choice). Requires `railway login` to have been run first.
- `scripts/run_f1_1_verifications.sh` — runs the 12 on-Railway verifications in order, captures output, halts on first failure, and appends results to `docs/f1.1-verification-log.md`.
- `docs/f1.1-verification-log.md` — the durable record of all 12 checks. Pre-filled with the GIVEN/WHEN/THEN from this plan, expected output, command to run, and a blank actual-result section per check. Filled in by the operator as checks complete.

**Local validation done:**
- Full test suite: 15/15 passing (`poetry run pytest`).
- `scripts/verify_build.py` SKIP path verified locally: `unset DATABASE_URL && poetry run python scripts/verify_build.py` → exit 0, `SKIP: db (DATABASE_URL not set)`, `RESULT: PASS`.
- `scripts/verify_build.py` FAIL path verified locally: `DATABASE_URL=postgresql://nobody:wrong@127.0.0.1:1/none?connect_timeout=2 poetry run python scripts/verify_build.py` → exit 1, `FAIL: db (OperationalError: ... connection refused)`, `RESULT: FAIL`.
- Gunicorn smoke: `poetry run gunicorn --bind 127.0.0.1:5050 sanity_app:app` → GET `/` returns 200 with body `ok`, HEAD `/` returns 200.
- Bash syntax check: both new scripts pass `bash -n`.

**Operator action (in order):**
1. `railway login` (one-time, opens a browser).
2. In the Railway dashboard, grant the GitHub integration admin access to `kayacohen/unifleet-v2-webapp` (one-time, browser step).
3. `bash scripts/provision_railway.sh` — provisions all four resources; halts on collision per the user's choice.
4. Open the dashboard, verify the Postgres region is `asia-southeast` and the Volume is attached to `web` at `/data` (the CLI cannot confirm these).
5. Link the GitHub repo in the dashboard's `web` service settings (this triggers the first deploy from commit `902c2dd`).
6. Wait for the deploy to reach "Success" (`railway logs` to watch).
7. `bash scripts/run_f1_1_verifications.sh` — runs the 12 checks, halts on first failure.
8. Fill in any remaining manual fields in `docs/f1.1-verification-log.md` (deploy IDs, sign-off).
9. Commit the completed log.

**Items this prep did NOT do (and why):**
- The actual `railway init` / `railway add` / `railway volume add` calls — they require a logged-in operator. The scripts issue them; they don't pre-execute them.
- The on-Railway verifications themselves — they require a live service. The runner script issues them; it doesn't pre-execute them.
- The "Stop and ask on collision" path was chosen per the user's T2 choice, so the provision script halts cleanly if the name is taken.

### Test Plan

This task's "tests" are a manual verification checklist. Each check is expressed as GIVEN/WHEN/THEN, same shape as automated tests, but executed by a human and recorded in the verification log.

#### Verification File(s)
- `docs/f1.1-verification-log.md` (new) — captures the result of every check, with timestamps, captured output, and the deploy ID for the persistence test. Alternatively, the same content goes in the T2 commit message body; the file is preferred because it survives future re-runs.

#### Verification Scenarios

##### Resource provisioning

- **`check_project_unifleet_exists`** — GIVEN the Railway dashboard is open WHEN the projects list is loaded THEN a project named exactly `unifleet` exists. Record the project ID.
- **`check_service_web_provisioned`** — GIVEN the `unifleet` project is open WHEN the service list is loaded THEN a service named exactly `web` exists. Record the service ID and the Railway-provided public URL.
- **`check_postgres_unifleet_provisioned`** — GIVEN the `unifleet` project is open WHEN the database list is loaded THEN a Postgres database named exactly `unifleet` exists AND its region is `asia-southeast` (Singapore). Record the database ID.
- **`check_volume_data_attached`** — GIVEN the `unifleet` project is open WHEN the volume list is loaded THEN a volume named `data` exists AND it shows as "Attached" to the `web` service AND its mount path is exactly `/data` AND its size is 8 GB (Hobby default). Record the volume ID.

##### Deploy pipeline

- **`check_github_integration_linked`** — GIVEN the `web` service's settings WHEN the "Source" / "Deploy" section is opened THEN the GitHub repository is listed AND auto-deploy on push to `main` is enabled. Record the linked repo.
- **`check_first_deploy_succeeds`** — GIVEN a no-op commit is pushed to `main` (e.g., a one-line README change) WHEN the Railway dashboard is observed THEN a new deploy starts within 30 seconds AND the deploy reaches a "Success" state within 3 minutes. Record the deploy ID and the start/end timestamps.

##### Application contract

- **`check_sanity_app_responds_200_on_get`** — GIVEN the `web` service is deployed WHEN `curl -i <railway-provided-url>/` is run from a developer shell THEN the response status is 200 AND the response body is non-empty. Record the response body (first 200 chars) and the full status line.
- **`check_sanity_app_responds_200_on_head`** — GIVEN the same URL WHEN `curl -I <railway-provided-url>/` is run THEN the response status is 200. Record the status line.

##### Build probe

- **`check_build_probe_passes_on_railway`** — GIVEN a Railway shell is open (via the dashboard or `railway shell`) WHEN `python scripts/verify_build.py` is run THEN exit code is 0 AND stdout contains `RESULT: PASS` AND every required dep is reported as `PASS` (not `SKIP`, not `FAIL`). Record the full stdout.
- **`check_build_probe_connects_to_postgres`** — GIVEN the probe output from the previous check WHEN the per-dep results are inspected THEN the `db` / `psycopg` line shows `PASS` (not `SKIP`), confirming `$DATABASE_URL` was auto-injected and `SELECT 1` succeeded. Record the relevant line.

##### Volume persistence

- **`check_volume_mount_path_is_data`** — GIVEN a Railway shell WHEN `mount | grep data` is run THEN the output includes a mount at `/data` (sanity check; the bigger test is the next one).
- **`check_volume_persists_across_redeploy`** — GIVEN a Railway shell WHEN the following sequence is run in order:
  1. `printf "f1.1-persistence-test-$(date +%s)" > /data/persistence_marker.txt`
  2. `cat /data/persistence_marker.txt` (record the content)
  3. Trigger a redeploy of the `web` service (push a no-op commit, or use the Railway dashboard "Redeploy" button)
  4. Wait for the new deploy to reach "Success"
  5. Open a fresh Railway shell and run `cat /data/persistence_marker.txt`
  THEN the marker content from step 5 must match the marker content from step 2 exactly. Record the original content, the redeploy ID, and the post-redeploy content.

##### Postgres connectivity

- **`check_psql_select_1_succeeds`** — GIVEN a Railway shell WHEN `psql "$DATABASE_URL" -c "SELECT 1"` is run THEN the output is `1` (or a tabular result with `1` in the first column) AND the exit code is 0. Record the output.

### Implementation Notes

- **Layer(s):** Infrastructure / operations. No code is written in T2.
- **Pattern reference:** The `.replit` file at lines 1-9 documents the existing Replit build setup; the same nix packages (`freetype`, `glibcLocales`) and gunicorn bind pattern must appear in `railway.toml`, which T1 produced.
- **Key decisions (from plan):** Project / service / DB / volume names and the `/data` mount path are locked from T1; if any of them collide with existing Railway resources on the developer's account, halt T2 and resolve the collision before proceeding (one of the plan's open questions).
- **Libraries:** None. T2 is operational; the libraries come from T1's `pyproject.toml` changes.
- **Tooling:** `railway` CLI (https://docs.railway.app/reference/cli) for the build-probe and psql checks; Railway dashboard for resource provisioning and shell access. If the CLI is not installed, the same operations can be done from the dashboard's "Shell" tab.
- **Order of operations matters:**
  1. Provision project, then service, then Postgres, then Volume (in that order; Volume must be created before it can be attached to the service).
  2. Attach the Volume to the `web` service with mount path `/data`.
  3. Connect the GitHub repo (this auto-triggers a first deploy).
  4. Run the verifications in the order listed above; a failure in an earlier check invalidates the later ones.

### Scope Boundaries

- DO NOT modify any code in the repo (T1 is the only code-bearing task in F1.1).
- DO NOT change DNS, environment variables beyond Railway's auto-injected `DATABASE_URL`, or the Replit deployment.
- DO NOT cut over production traffic; `unifleet.asia` is still pointing at Replit.
- DO NOT enable any Railway-side observability/analytics beyond the defaults.
- DO NOT add a CI workflow (the GitHub-deploy integration IS the CI for F1.1; F3.2 introduces real test gates).
- Only: provision resources, attach the Volume, link GitHub, run the verifications, and document the results.

### Files Expected

**New files:**
- `docs/f1.1-verification-log.md` — captures every check's result, with timestamps and captured output. This is the durable record that the plan's "Definition of done" was met.

**Modified files:**
- None. T2 does not touch the codebase.

**Must NOT modify:**
- Everything T1 produced (`railway.toml`, `sanity_app.py`, `scripts/verify_build.py`, the tests, the `pyproject.toml` changes).
- All F1.1-out-of-scope files (everything else in the repo, including `main.py` and the data files).

### TDD Sequence

Not applicable. T2 is operational work; its "tests" are the manual verifications listed above. The order of verifications matters: an early failure should halt T2 rather than letting later checks run on a broken platform.

### Open Questions Re-Surfaced

- **Project name collision** — if a `unifleet` project already exists on the Railway account, T2 cannot proceed with the locked name. Resolution paths: (a) rename the existing project and reuse the name, (b) pick a documented alternative prefix, (c) use a separate Railway account for this migration. **Default if collision is hit:** (b) — append a suffix like `-v2` to the project name only; the service, DB, and volume names stay as planned.
- **GitHub access for the Railway integration** — the integration needs admin-equivalent access to the repo. If the developer doesn't have it, the build can be triggered manually via the Railway CLI from a machine that does. **Default if blocked:** manual deploys for F1.1, with the GitHub integration configured retroactively once access is granted.

---

_Review both task specs above, then run the tdd skill against Task T1 first:_
_"Implement task T1 from specs/plans/PLAN-railway-provisioning.md"_
_T2 starts after T1's test suite is green and the smoke check on a developer laptop passes._
