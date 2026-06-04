#!/usr/bin/env bash
# scripts/provision_railway.sh
#
# Provision the Railway resources for F1.1:
#   - project: unifleet
#   - service: web (linked to the GitHub repo)
#   - postgres: unifleet (asia-southeast)
#   - volume: data (mounted at /data on the web service)
#
# Per the plan's locked decisions and the user's T2 choices:
#   - Stops and asks the user on `unifleet` project-name collision
#   - Uses the T1 commit (902c2dd, already on origin/main) as the first deploy
#
# Prerequisites:
#   1. railway CLI 4.x installed (verified at script start)
#   2. User logged in: `railway login` (the CLI will open a browser)
#   3. The GitHub integration has been granted admin access to kayacohen/unifleet-v2-webapp
#      (this is a one-time browser step; do it before running this script)
#
# Idempotency:
#   - Re-running the script after a partial failure resumes from the next step
#   - It will NOT recreate or rename anything that already exists
#   - The Volume's `data` name is treated as a stable identifier

set -euo pipefail

# ---------- colors for readability ----------
RED=$'\033[0;31m'
GRN=$'\033[0;32m'
YLW=$'\033[1;33m'
BLU=$'\033[0;34m'
RST=$'\033[0m'

info()  { printf '%s==>%s %s\n' "$BLU" "$RST" "$*"; }
ok()    { printf '%s  ✓%s %s\n' "$GRN" "$RST" "$*"; }
warn()  { printf '%s  !%s %s\n' "$YLW" "$RST" "$*"; }
err()   { printf '%s  ✗%s %s\n' "$RED" "$RST" "$*" >&2; }
halt()  { err "$*"; exit 1; }

# ---------- preflight: CLI version + auth ----------
info "Preflight: checking railway CLI"
railway --version >/dev/null || halt "railway CLI not found. Install: https://docs.railway.app/reference/cli"

if ! railway whoami >/dev/null 2>&1; then
  halt "Not logged in to Railway. Run: railway login  (then re-run this script)"
fi
ok "Logged in as: $(railway whoami)"

# ---------- preflight: project-name collision ----------
info "Checking for `unifleet` project-name collision"
PROJECT_LIST="$(railway list 2>&1 || true)"
if printf '%s\n' "$PROJECT_LIST" | grep -Eq '(^|[[:space:]])unifleet([[:space:]]|$)'; then
  err "A project named `unifleet` already exists on this Railway account."
  err "Per the T2 open-questions default, this script STOPS and asks for a decision."
  err ""
  err "Existing project listing:"
  printf '%s\n' "$PROJECT_LIST" | sed 's/^/    /'
  err ""
  err "Resolution options (per PLAN-railway-provisioning.md open-questions):"
  err "  (a) Rename the existing project in the Railway dashboard, then re-run this script."
  err "  (b) Use a different project name (e.g., unifleet-v2) and update the docs."
  err "  (c) Use a separate Railway account for this migration."
  err ""
  halt "Collision; user must decide. No resources were created."
fi
ok "No `unifleet` project collision"

# ---------- step 1: create project ----------
info "Step 1/5: create project `unifleet`"
if railway status >/dev/null 2>&1; then
  warn "Project already linked; skipping `railway init`"
  warn "Linked to: $(railway status 2>&1 | head -1)"
else
  railway init -n unifleet
  ok "Project created and linked"
fi

# ---------- step 2: add service `web` linked to the GitHub repo ----------
info "Step 2/5: add service `web` (linked to GitHub repo)"
if railway status 2>&1 | grep -q 'Service:.*web'; then
  warn "Service `web` already exists; skipping `railway add`"
else
  railway add -s web -r github.com/kayacohen/unifleet-v2-webapp
  ok "Service `web` created and linked to GitHub repo"
fi

# ---------- step 3: add Postgres database `unifleet` ----------
info "Step 3/5: add Postgres database `unifleet`"
POSTGRES_EXISTS="$(railway status 2>&1 | grep -E 'Plugin|Plugin\.|Database|unifleet' || true)"
# `railway add -d postgres` is idempotent for the same name in the same project;
# if a DB with this name already exists, the CLI exits non-zero with a clear message.
# We try, and if it fails with "already exists" we treat that as success.
if printf '%s' "$POSTGRES_EXISTS" | grep -qi 'unifleet'; then
  warn "Postgres `unifleet` already exists; skipping `railway add -d postgres`"
else
  if ! railway add -d postgres 2>&1 | tee /tmp/railway-add-postgres.log; then
    if grep -qi 'already exists' /tmp/railway-add-postgres.log; then
      warn "Postgres already exists; continuing"
    else
      halt "Failed to add Postgres; see /tmp/railway-add-postgres.log"
    fi
  fi
  ok "Postgres added (region defaults to asia-southeast per workspace; verify in dashboard)"
fi
warn "Verify Postgres region is `asia-southeast` in the dashboard. Hobby plan region availability varies."

# ---------- step 4: add Volume `data` with mount path `/data` ----------
info "Step 4/5: add Volume `data` (mount path: /data)"
VOLUME_LIST="$(railway volume list 2>&1 || true)"
if printf '%s' "$VOLUME_LIST" | grep -q 'data'; then
  warn "Volume `data` already exists; skipping `railway volume add`"
  warn "Verify its mount path is `/data` in the dashboard (re-attach if needed)"
else
  railway volume add -m /data
  ok "Volume `data` added with mount path /data"
fi

# ---------- step 5: attach Volume `data` to the `web` service ----------
info "Step 5/5: attach Volume `data` to service `web`"
ATTACH_OUTPUT="$(railway volume attach -v data 2>&1 || true)"
if printf '%s' "$ATTACH_OUTPUT" | grep -qi 'already attached'; then
  warn "Volume `data` already attached to a service; verify it is attached to `web` in the dashboard"
else
  printf '%s\n' "$ATTACH_OUTPUT"
  ok "Volume `data` attached (verify in dashboard that it is on `web`, not a different service)"
fi

# ---------- summary + next steps ----------
echo
info "Provisioning complete. Status:"
railway status
echo
info "Next steps (operator does these by hand):"
echo
echo "  1. Open the Railway dashboard:"
echo "       railway open"
echo
echo "  2. Verify the Postgres region is `asia-southeast` (check 1.3 in the verification log)."
echo "     If it defaulted to a different region, delete the Postgres and recreate with the right region."
echo
echo "  3. Verify the Volume is attached to the `web` service with mount path /data (check 1.4)."
echo
echo "  4. Linking the GitHub repo via the dashboard triggers the first deploy from commit 902c2dd."
echo "     Watch the deploy:  railway logs"
echo
echo "  5. Once the deploy succeeds, run the 12 on-Railway verifications:"
echo "       bash scripts/run_f1_1_verifications.sh"
echo
echo "  6. Fill in the results in:"
echo "       docs/f1.1-verification-log.md"
echo
ok "Done."
