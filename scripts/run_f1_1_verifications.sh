#!/usr/bin/env bash
# scripts/run_f1_1_verifications.sh
#
# Run the 12 on-Railway verifications for F1.1 in order, capture output,
# and append results to docs/f1.1-verification-log.md.
#
# Halts on the first FAIL (an early failure invalidates later checks, per the plan).
#
# Prerequisites:
#   - bash scripts/provision_railway.sh has been run successfully
#   - The user is still logged in to Railway (railway whoami)
#   - The first deploy from commit 902c2dd has reached "Success"
#
# What this script does NOT automate:
#   - Check 1.3 (Postgres region): the CLI does not yet expose DB region
#   - Check 2.1 (GitHub integration linked): browser step only
#   - Some deploy-ID / URL capture: filled in by the user from CLI output

set -euo pipefail

RED=$'\033[0;31m'
GRN=$'\033[0;32m'
YLW=$'\033[1;33m'
BLU=$'\033[0;34m'
RST=$'\033[0m'

info()  { printf '%s==>%s %s\n' "$BLU" "$RST" "$*"; }
ok()    { printf '%s  ✓%s %s\n' "$GRN" "$RST" "$*"; }
warn()  { printf '%s  !%s %s\n' "$YLW" "$RST" "$*"; }
err()   { printf '%s  ✗%s %s\n' "$RED" "$RST" "$*" >&2; }

# ---------- preflight ----------
info "Preflight"
railway --version >/dev/null || { err "railway CLI not found"; exit 1; }
railway whoami >/dev/null 2>&1 || { err "Not logged in: run `railway login` first"; exit 1; }
railway status >/dev/null 2>&1 || { err "No linked project; run scripts/provision_railway.sh first"; exit 1; }
ok "Logged in and linked to a project"

LOG="docs/f1.1-verification-log.md"
[ -f "$LOG" ] || { err "$LOG not found; create it first"; exit 1; }

# Helper: append a check result to the log
log_check() {
  local num="$1" name="$2" result="$3" output="$4"
  cat >> "$LOG" <<EOF

#### Auto-captured $(date -u +%Y-%m-%dT%H:%M:%SZ) — check $num: $name

**Auto result:** $result

\`\`\`
$output
\`\`\`

EOF
}

# Capture the Railway-provided URL once (used by curl checks)
RAILWAY_URL="$(railway status 2>&1 | grep -Eoi 'https://[a-z0-9-]+\.up\.railway\.app' | head -1 || true)"
if [ -z "$RAILWAY_URL" ]; then
  warn "Could not auto-detect public URL from \`railway status\`."
  warn "Fill in RAILWAY_URL when running checks 3.1, 3.2."
  warn "Find it in the dashboard: project → web service → Settings → Domains."
  RAILWAY_URL="https://REPLACE-ME.up.railway.app"
fi
info "Using public URL: $RAILWAY_URL"

# ---------- 1.1 check_project_unifleet_exists ----------
info "Check 1.1: project `unifleet` exists"
OUT="$(railway list 2>&1 || true)"
if printf '%s' "$OUT" | grep -Eq '(^|[[:space:]])unifleet([[:space:]]|$)'; then
  ok "PASS"
  log_check "1.1" "check_project_unifleet_exists" "PASS" "$OUT"
else
  err "FAIL: no project named `unifleet`"
  log_check "1.1" "check_project_unifleet_exists" "FAIL" "$OUT"
  exit 1
fi

# ---------- 1.2 check_service_web_provisioned ----------
info "Check 1.2: service `web` exists"
OUT="$(railway status 2>&1 || true)"
if printf '%s' "$OUT" | grep -qi 'Service:.*web'; then
  ok "PASS"
  log_check "1.2" "check_service_web_provisioned" "PASS" "$OUT"
else
  err "FAIL: no service named `web`"
  log_check "1.2" "check_service_web_provisioned" "FAIL" "$OUT"
  exit 1
fi

# ---------- 1.3 check_postgres_unifleet_provisioned (manual: region check) ----------
info "Check 1.3: Postgres `unifleet` provisioned (region: asia-southeast)"
warn "Region check requires the dashboard; the CLI does not yet expose DB region."
warn "Verify in: Dashboard → unifleet project → Postgres tile → region should read `asia-southeast`."
echo "Press ENTER once you've verified, or Ctrl-C to abort."
read -r _

# ---------- 1.4 check_volume_data_attached ----------
info "Check 1.4: Volume `data` attached to web at /data"
OUT="$(railway volume list 2>&1 || true)"
if printf '%s' "$OUT" | grep -q 'data'; then
  ok "PASS (verify mount path /data in dashboard)"
  log_check "1.4" "check_volume_data_attached" "PASS (verify mount path in dashboard)" "$OUT"
else
  err "FAIL: no volume named `data`"
  log_check "1.4" "check_volume_data_attached" "FAIL" "$OUT"
  exit 1
fi
warn "Open the dashboard and confirm mount path is exactly /data."

# ---------- 2.1 check_github_integration_linked (manual) ----------
info "Check 2.1: GitHub integration linked"
warn "Browser step only. Open the dashboard → web service → Settings → Source."
warn "Verify: repo `kayacohen/unifleet-v2-webapp` is listed, auto-deploy on push to main is enabled."
echo "Press ENTER once verified, or Ctrl-C to abort."
read -r _

# ---------- 2.2 check_first_deploy_succeeds ----------
info "Check 2.2: first deploy succeeded (T1 commit 902c2dd)"
OUT="$(railway deployment list 2>&1 || true)"
if printf '%s' "$OUT" | grep -Eq 'SUCCESS|success'; then
  ok "PASS (at least one successful deployment; verify it's from 902c2dd)"
  log_check "2.2" "check_first_deploy_succeeds" "PASS" "$OUT"
else
  err "FAIL: no successful deployment yet"
  err "Run \`railway logs\` to see why the build is failing."
  log_check "2.2" "check_first_deploy_succeeds" "FAIL" "$OUT"
  exit 1
fi
warn "Record the deploy ID, start, and end timestamps in the log manually."

# ---------- 3.1 check_sanity_app_responds_200_on_get ----------
info "Check 3.1: GET / returns 200"
if [ "$RAILWAY_URL" = "https://REPLACE-ME.up.railway.app" ]; then
  err "Skipping: RAILWAY_URL not set. Edit this script or set the env var."
  exit 1
fi
OUT="$(curl -i -s "$RAILWAY_URL/" 2>&1 || true)"
if printf '%s' "$OUT" | head -1 | grep -q '200'; then
  ok "PASS"
  log_check "3.1" "check_sanity_app_responds_200_on_get" "PASS" "$OUT"
else
  err "FAIL: GET / did not return 200"
  log_check "3.1" "check_sanity_app_responds_200_on_get" "FAIL" "$OUT"
  exit 1
fi

# ---------- 3.2 check_sanity_app_responds_200_on_head ----------
info "Check 3.2: HEAD / returns 200"
OUT="$(curl -I -s "$RAILWAY_URL/" 2>&1 || true)"
if printf '%s' "$OUT" | head -1 | grep -q '200'; then
  ok "PASS"
  log_check "3.2" "check_sanity_app_responds_200_on_head" "PASS" "$OUT"
else
  err "FAIL: HEAD / did not return 200"
  log_check "3.2" "check_sanity_app_responds_200_on_head" "FAIL" "$OUT"
  exit 1
fi

# ---------- 4.1 check_build_probe_passes_on_railway ----------
info "Check 4.1: build probe passes on Railway"
OUT="$(railway run python scripts/verify_build.py 2>&1 || true)"
if printf '%s' "$OUT" | grep -q 'RESULT: PASS'; then
  ok "PASS"
  log_check "4.1" "check_build_probe_passes_on_railway" "PASS" "$OUT"
else
  err "FAIL: build probe did not return RESULT: PASS"
  log_check "4.1" "check_build_probe_passes_on_railway" "FAIL" "$OUT"
  exit 1
fi

# ---------- 4.2 check_build_probe_connects_to_postgres ----------
info "Check 4.2: build probe connects to Postgres (db line is PASS, not SKIP)"
if printf '%s' "$OUT" | grep -Eq '^PASS:[[:space:]]+db'; then
  ok "PASS"
  DB_LINE="$(printf '%s' "$OUT" | grep -E '^PASS:[[:space:]]+db')"
  log_check "4.2" "check_build_probe_connects_to_postgres" "PASS" "$DB_LINE"
else
  err "FAIL: db line is not PASS"
  DB_LINE="$(printf '%s' "$OUT" | grep -E '^(PASS|FAIL|SKIP):[[:space:]]+db')"
  log_check "4.2" "check_build_probe_connects_to_postgres" "FAIL" "$DB_LINE"
  exit 1
fi

# ---------- 5.1 check_volume_mount_path_is_data ----------
info "Check 5.1: /data is mounted on the running container"
OUT="$(railway ssh -- 'mount | grep data' 2>&1 || true)"
if printf '%s' "$OUT" | grep -q '/data'; then
  ok "PASS"
  log_check "5.1" "check_volume_mount_path_is_data" "PASS" "$OUT"
else
  err "FAIL: /data not mounted"
  log_check "5.1" "check_volume_mount_path_is_data" "FAIL" "$OUT"
  exit 1
fi

# ---------- 5.2 check_volume_persists_across_redeploy ----------
info "Check 5.2: Volume persists across redeploy"
MARKER="f1.1-persistence-test-$(date +%s)"
info "Writing marker: $MARKER"
WRITE_OUT="$(railway ssh -- "printf '$MARKER' > /data/persistence_marker.txt && cat /data/persistence_marker.txt" 2>&1 || true)"
info "Read back: $WRITE_OUT"
warn "About to trigger a redeploy. This will restart the web service."
echo "Press ENTER to redeploy, or Ctrl-C to abort."
read -r _
info "Triggering redeploy"
railway redeploy 2>&1 | tail -5 || true
warn "Wait for the redeploy to reach 'Success' (watch with: railway logs)"
echo "Press ENTER once the redeploy is Success."
read -r _
READ_OUT="$(railway ssh -- cat /data/persistence_marker.txt 2>&1 || true)"
info "Post-redeploy read: $READ_OUT"
if [ "$WRITE_OUT" = "$READ_OUT" ] && [ -n "$READ_OUT" ]; then
  ok "PASS"
  log_check "5.2" "check_volume_persists_across_redeploy" "PASS" "Original: $WRITE_OUT / Post-redeploy: $READ_OUT"
else
  err "FAIL: marker content changed across redeploy"
  err "Original:    $WRITE_OUT"
  err "Post-redeploy: $READ_OUT"
  log_check "5.2" "check_volume_persists_across_redeploy" "FAIL" "Original: $WRITE_OUT / Post-redeploy: $READ_OUT"
  exit 1
fi

# ---------- 6.1 check_psql_select_1_succeeds ----------
info "Check 6.1: psql \$DATABASE_URL -c 'SELECT 1' returns 1"
OUT="$(railway run psql "$DATABASE_URL" -c "SELECT 1" 2>&1 || true)"
# Match a tabular row like " ?column? \n----------+\n         1" or a bare "1"
if printf '%s' "$OUT" | grep -Eq '^[[:space:]]*1[[:space:]]*$'; then
  ok "PASS"
  log_check "6.1" "check_psql_select_1_succeeds" "PASS" "$OUT"
else
  err "FAIL: SELECT 1 did not return 1"
  log_check "6.1" "check_psql_select_1_succeeds" "FAIL" "$OUT"
  exit 1
fi

# ---------- summary ----------
ok "All 12 checks passed."
info "Final output appended to: $LOG"
info "Review the log and fill in any manual fields (deploy IDs, IDs, sign-off, etc.)"
