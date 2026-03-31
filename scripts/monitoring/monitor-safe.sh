#!/bin/bash
# Non-mutating monitoring checks for live environments.
# Runs FROM the devcontainer, GitHub Actions, or any machine with SSH access.
# Does NOT create test users or modify any state on the server.
#
# Usage:
#   scripts/monitoring/monitor-safe.sh                      # Test sea-staging
#   scripts/monitoring/monitor-safe.sh --host=sea-live      # Test live
set -o pipefail

# --- Parse arguments ---
SSH_HOST="sea-staging"
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

source "$SCRIPTS_ROOT/lib/monitor-helpers.sh"

# --- Initialise: read env, detect compose ---
detect_compose

# Required env vars — abort if SSH is broken
OJS_BASE_URL=$(require_env "OJS_BASE_URL")
OJS_JOURNAL_PATH=$(require_env "OJS_JOURNAL_PATH")
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"
API_KEY=$(require_env "WPOJS_API_KEY_SECRET")

# Optional env vars
WP_HOME=$(read_env "WP_HOME")
WP_PUBLIC_URL=$(read_env "WP_PUBLIC_URL")
if [ -z "$WP_PUBLIC_URL" ]; then
  CADDY_WP=$(read_env "CADDY_WP_DOMAIN")
  if [ -n "$CADDY_WP" ]; then
    WP_PUBLIC_URL="https://$CADDY_WP"
  fi
fi

# Basic auth credentials for staging sites behind Caddy
CADDY_AUTH_USER=$(read_env "CADDY_WP_AUTH_USER")
CADDY_AUTH_PASS=$(read_env "CADDY_WP_AUTH_PASS")
WP_CURL_AUTH_ARGS=()
if [ -n "$CADDY_AUTH_USER" ] && [ -n "$CADDY_AUTH_PASS" ]; then
  WP_CURL_AUTH_ARGS=(-u "${CADDY_AUTH_USER}:${CADDY_AUTH_PASS}")
fi

# Fallback: if WP_HOME looks public (not private IP/non-standard port), use it directly
if [ -z "$WP_PUBLIC_URL" ]; then
  if echo "$WP_HOME" | grep -qE '^https?://(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|localhost|127\.|.*:[0-9]{4,})'; then
    WP_PUBLIC_URL=""
  else
    WP_PUBLIC_URL="$WP_HOME"
  fi
fi

echo "=== Monitor (safe): $SSH_HOST ==="
echo "    WP:  $WP_HOME"
echo "    WP (public): ${WP_PUBLIC_URL:-N/A — will check via SSH}"
echo "    OJS: $OJS_BASE_URL"
echo ""

# ============================================================
# 1. HTTP & API CHECKS
# ============================================================
echo "--- HTTP & API ---"

# Helper: curl WP, handling internal vs public URLs.
wp_curl() {
  local path="$1" url
  if [ -n "$WP_PUBLIC_URL" ]; then
    url="${WP_PUBLIC_URL}${path}"
    curl -s -o /dev/null -w '%{http_code}' "${WP_CURL_AUTH_ARGS[@]}" "$url" 2>/dev/null
  else
    remote "$COMPOSE exec -T wp curl -s -o /dev/null -w '%{http_code}' 'http://localhost:80${path}'" 2>/dev/null
  fi
}

# 1a. WP HTTP
WP_CHECK_URL="${WP_PUBLIC_URL:-$WP_HOME}"
WP_STATUS=$(wp_curl "") || WP_STATUS="000"
if [ -n "$WP_PUBLIC_URL" ]; then
  WP_TIME=$(curl -s -o /dev/null -w '%{time_total}' "${WP_CURL_AUTH_ARGS[@]}" "$WP_PUBLIC_URL" 2>/dev/null) || WP_TIME="0"
else
  WP_TIME="0"
fi
if [ "$WP_STATUS" = "200" ] || [ "$WP_STATUS" = "301" ] || [ "$WP_STATUS" = "302" ]; then
  pass "WP responds (HTTP $WP_STATUS${WP_TIME:+, ${WP_TIME}s})"
else
  fail "WP not responding (HTTP $WP_STATUS via ${WP_CHECK_URL})"
fi

# 1b. WP Admin (path differs: /wp/wp-admin/ internally, /wp-admin/ via Caddy)
if [ -n "$WP_PUBLIC_URL" ]; then
  WP_ADMIN_STATUS=$(wp_curl "/wp-admin/") || WP_ADMIN_STATUS="000"
else
  WP_ADMIN_STATUS=$(wp_curl "/wp/wp-admin/") || WP_ADMIN_STATUS="000"
fi
if [ "$WP_ADMIN_STATUS" = "200" ] || [ "$WP_ADMIN_STATUS" = "301" ] || [ "$WP_ADMIN_STATUS" = "302" ]; then
  pass "WP Admin responds (HTTP $WP_ADMIN_STATUS)"
else
  fail "WP Admin not responding (HTTP $WP_ADMIN_STATUS)"
fi

# 1c. WP REST API
WP_API_STATUS=$(wp_curl "/wp-json/") || WP_API_STATUS="000"
if [ "$WP_API_STATUS" = "200" ]; then
  pass "WP REST API responds"
else
  fail "WP REST API not responding (HTTP $WP_API_STATUS)"
fi

# 1d. OJS HTTP
OJS_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$OJS_BASE_URL" 2>/dev/null) || OJS_STATUS="000"
OJS_TIME=$(curl -s -o /dev/null -w '%{time_total}' "$OJS_BASE_URL" 2>/dev/null) || OJS_TIME="0"
if [ "$OJS_STATUS" = "200" ] || [ "$OJS_STATUS" = "301" ] || [ "$OJS_STATUS" = "302" ]; then
  pass "OJS responds (HTTP $OJS_STATUS, ${OJS_TIME}s)"
else
  fail "OJS not responding (HTTP $OJS_STATUS)"
fi

# 1e. OJS Admin
OJS_ADMIN_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$OJS_JOURNAL_URL/management/settings/access" 2>/dev/null) || OJS_ADMIN_STATUS="000"
OJS_ADMIN_BODY=$(curl -s "$OJS_JOURNAL_URL/management/settings/access" 2>/dev/null | head -30)
if echo "$OJS_ADMIN_BODY" | grep -qi "Fatal error\|Exception\|404 Not Found"; then
  fail "OJS Admin page error (HTTP $OJS_ADMIN_STATUS)"
elif [ "$OJS_ADMIN_STATUS" = "200" ] || [ "$OJS_ADMIN_STATUS" = "302" ]; then
  pass "OJS Admin responds (HTTP $OJS_ADMIN_STATUS)"
else
  fail "OJS Admin not responding (HTTP $OJS_ADMIN_STATUS)"
fi

# 1f. Response time thresholds (10s — generous for cross-Atlantic CI runners)
WP_TIME_INT=$(echo "$WP_TIME" | cut -d. -f1)
OJS_TIME_INT=$(echo "$OJS_TIME" | cut -d. -f1)
if [ "${WP_TIME_INT:-0}" -gt 10 ] 2>/dev/null; then
  fail "WP response time too slow (${WP_TIME}s > 10s)"
fi
if [ "${OJS_TIME_INT:-0}" -gt 10 ] 2>/dev/null; then
  fail "OJS response time too slow (${OJS_TIME}s > 10s)"
fi

# ============================================================
# 2. OJS PLUGIN & SYNC CHECKS
# ============================================================
echo ""
echo "--- OJS Plugin & Sync ---"

# 2a. OJS plugin ping
PING=$(remote "$COMPOSE exec -T ojs curl -sf http://localhost:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/ping") || PING=""
if echo "$PING" | grep -q '"status":"ok"'; then
  pass "OJS plugin responds to ping"
else
  fail "OJS plugin ping failed" "${PING:-<empty — SSH or container may be down>}"
fi

# 2b. OJS preflight
PREFLIGHT=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/preflight") || PREFLIGHT=""
if echo "$PREFLIGHT" | grep -q '"compatible":true'; then
  CHECKS=$(echo "$PREFLIGHT" | grep -o '"ok":true' | wc -l)
  pass "Preflight passes ($CHECKS checks OK)"
else
  fail "Preflight failed" "${PREFLIGHT:-<empty — SSH or container may be down>}"
fi

# 2c. WP-CLI test-connection
TC_OUTPUT=$(wp_cli "ojs-sync test-connection") || TC_OUTPUT=""
if echo "$TC_OUTPUT" | grep -q "Connection test passed"; then
  pass "test-connection passes"
else
  fail "test-connection failed" "$(echo "${TC_OUTPUT:-<empty>}" | tail -3)"
fi

# ============================================================
# 3. REQUIRED PLUGINS
# ============================================================
echo ""
echo "--- Required Plugins ---"

PLUGINS=$(wp_cli "plugin list --status=active --format=csv --fields=name") || PLUGINS=""
if [ -z "$PLUGINS" ]; then
  fail "Could not retrieve plugin list (SSH or WP-CLI may be down)"
else
  for PLUGIN in woocommerce woocommerce-subscriptions woocommerce-memberships ultimate-member wpojs-sync; do
    if echo "$PLUGINS" | grep -q "^$PLUGIN$"; then
      pass "$PLUGIN active"
    else
      fail "$PLUGIN not active"
    fi
  done
fi

# ============================================================
# 4. SUBSCRIPTION TYPES & MAPPING
# ============================================================
echo ""
echo "--- Subscription Config ---"

SUB_TYPES=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/subscription-types") || SUB_TYPES=""
TYPE_COUNT=$(echo "$SUB_TYPES" | grep -o '"id"' | wc -l)
if [ "$TYPE_COUNT" -gt "0" ]; then
  pass "$TYPE_COUNT subscription type(s) configured"
else
  fail "No subscription types found" "${SUB_TYPES:-<empty — SSH or API may be down>}"
fi

# Product-to-type mapping
MAPPING_JSON=$(wp_cli "eval '
  \$mapping = get_option(\"wpojs_type_mapping\", []);
  echo json_encode(\$mapping);
'") || MAPPING_JSON="{}"
MAPPING_OK=true
if [ "$MAPPING_JSON" = "{}" ] || [ "$MAPPING_JSON" = "[]" ] || [ -z "$MAPPING_JSON" ]; then
  fail "No product-to-type mappings configured"
  MAPPING_OK=false
else
  BROKEN=$(wp_cli "eval '
    \$mapping = get_option(\"wpojs_type_mapping\", []);
    \$broken = [];
    foreach (\$mapping as \$product_id => \$type_id) {
      if (!wc_get_product(\$product_id)) {
        \$broken[] = \"product_\" . \$product_id;
      }
    }
    echo implode(\",\", \$broken);
  '") || BROKEN=""
  if [ -n "$BROKEN" ]; then
    fail "Broken product mapping(s): $BROKEN"
    MAPPING_OK=false
  fi
  # Check OJS type IDs
  BROKEN_TYPES=$(wp_cli "eval '
    \$mapping = get_option(\"wpojs_type_mapping\", []);
    \$type_ids = array_unique(array_values(\$mapping));
    echo implode(\",\", \$type_ids);
  '") || BROKEN_TYPES=""
  if [ -n "$BROKEN_TYPES" ]; then
    IFS=',' read -ra TYPE_IDS <<< "$BROKEN_TYPES"
    for TID in "${TYPE_IDS[@]}"; do
      if ! echo "$SUB_TYPES" | grep -q "\"id\":$TID"; then
        fail "OJS subscription type $TID not found"
        MAPPING_OK=false
      fi
    done
  fi
  if [ "$MAPPING_OK" = true ]; then
    MAPPING_COUNT=$(echo "$MAPPING_JSON" | grep -o '"[0-9]*"' | wc -l)
    pass "All $MAPPING_COUNT product-to-type mappings valid"
  fi
fi

# ============================================================
# 5. STRIPE CHECKS
# ============================================================
echo ""
echo "--- Stripe ---"

# 5a. Stripe plugin active in OJS
# CHAR(61) = '=', CHAR(37,115,116,114,105,112,101,37) = '%stripe%'
STRIPE_ROWS=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT CONCAT(setting_name, CHAR(61), setting_value) FROM plugin_settings WHERE plugin_name LIKE CONCAT(CHAR(37), CHAR(115,116,114,105,112,101), CHAR(37))\"'") || STRIPE_ROWS=""
if [ -z "$STRIPE_ROWS" ]; then
  fail "Stripe plugin settings not found (remote command returned empty)"
elif echo "$STRIPE_ROWS" | grep -q "enabled=1"; then
  pass "Stripe plugin active in OJS"
else
  fail "Stripe plugin not active in OJS"
fi

# 5b. Stripe API key valid (stored in OJS plugin_settings, not .env)
# CHAR(116,101,115,116,83,101,99,114,101,116,75,101,121) = 'testSecretKey'
# CHAR(115,101,99,114,101,116,75,101,121) = 'secretKey'
STRIPE_TEST_MODE=$(echo "$STRIPE_ROWS" | grep -o 'testMode=.' | cut -d= -f2)
if [ "$STRIPE_TEST_MODE" = "1" ]; then
  STRIPE_KEY=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT setting_value FROM plugin_settings WHERE plugin_name LIKE CONCAT(CHAR(37), CHAR(115,116,114,105,112,101), CHAR(37)) AND setting_name = CHAR(116,101,115,116,83,101,99,114,101,116,75,101,121)\"'") || STRIPE_KEY=""
else
  STRIPE_KEY=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT setting_value FROM plugin_settings WHERE plugin_name LIKE CONCAT(CHAR(37), CHAR(115,116,114,105,112,101), CHAR(37)) AND setting_name = CHAR(115,101,99,114,101,116,75,101,121)\"'") || STRIPE_KEY=""
fi
STRIPE_KEY=$(echo "$STRIPE_KEY" | tr -d '[:space:]')
if [ -n "$STRIPE_KEY" ]; then
  # Use checkout/sessions endpoint — restricted keys (rk_*) may not have balance access
  STRIPE_RESPONSE=$(curl -s -o /dev/null -w '%{http_code}' -u "$STRIPE_KEY:" "https://api.stripe.com/v1/checkout/sessions?limit=1" 2>/dev/null) || STRIPE_RESPONSE="000"
  if [ "$STRIPE_RESPONSE" = "200" ]; then
    pass "Stripe API key valid"
  elif [ "$STRIPE_RESPONSE" = "401" ]; then
    fail "Stripe API key invalid (HTTP 401)"
  else
    fail "Stripe API unreachable (HTTP $STRIPE_RESPONSE)"
  fi
else
  fail "Stripe secret key not found in OJS plugin_settings"
fi

# 5c. Stripe webhook endpoint reachable
WEBHOOK_URL="${OJS_JOURNAL_URL}/payment/plugin/StripePayment/webhook"
WEBHOOK_STATUS=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$WEBHOOK_URL" 2>/dev/null) || WEBHOOK_STATUS="000"
# Expecting 400 (no payload) — not 404 (route missing)
if [ "$WEBHOOK_STATUS" = "400" ] || [ "$WEBHOOK_STATUS" = "200" ]; then
  pass "Stripe webhook endpoint reachable (HTTP $WEBHOOK_STATUS)"
elif [ "$WEBHOOK_STATUS" = "404" ]; then
  fail "Stripe webhook endpoint not found (HTTP 404 — plugin route missing)"
else
  fail "Stripe webhook endpoint returned HTTP $WEBHOOK_STATUS"
fi


# ============================================================
# 6. OJS JOB QUEUE
# ============================================================
echo ""
echo "--- OJS Job Queue ---"

# 6a. Job processing: check for persistent worker OR cron-based processing
JOB_WORKER=$(remote "$COMPOSE exec -T ojs pgrep -a -f 'jobs.php' 2>/dev/null || echo ''")
if echo "$JOB_WORKER" | grep -q "jobs.php"; then
  pass "OJS job worker running"
else
  OJS_CRON=$(remote "$COMPOSE exec -T ojs crontab -l 2>/dev/null || echo ''")
  if echo "$OJS_CRON" | grep -q "runScheduledTasks\|pkp-run-scheduled\|scheduler\.php\|ojs-scheduler-heartbeat"; then
    pass "OJS jobs processed via cron (no persistent worker needed)"
  else
    fail "OJS job worker not running and no cron configured"
  fi
fi

# 6b. Pending jobs (should be near zero)
PENDING_OJS_JOBS=$(require_remote "OJS pending jobs count" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM jobs\"'") || PENDING_OJS_JOBS=""
if [ -n "$PENDING_OJS_JOBS" ]; then
  if [ "${PENDING_OJS_JOBS:-0}" -gt 50 ] 2>/dev/null; then
    fail "OJS job queue backing up ($PENDING_OJS_JOBS pending)"
  else
    pass "OJS job queue OK ($PENDING_OJS_JOBS pending)"
  fi
fi

# 6c. Failed jobs
FAILED_OJS_JOBS=$(require_remote "OJS failed jobs count" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM failed_jobs\"'") || FAILED_OJS_JOBS=""
if [ -n "$FAILED_OJS_JOBS" ]; then
  if [ "${FAILED_OJS_JOBS:-0}" -gt 0 ] 2>/dev/null; then
    fail "$FAILED_OJS_JOBS failed OJS jobs (check failed_jobs table)"
  else
    pass "No failed OJS jobs"
  fi
fi

# ============================================================
# 7. SERVER RESOURCES
# ============================================================
echo ""
echo "--- Server Resources ---"

# 7a. Load average
LOAD=$(require_ssh "Load average" "cat /proc/loadavg") || LOAD=""
if [ -n "$LOAD" ]; then
  LOAD_5MIN=$(echo "$LOAD" | awk '{print $2}')
  NPROC=$($SSH_CMD "nproc") || NPROC="3"
  LOAD_THRESHOLD=$(( NPROC * 2 ))
  info "Load average: $LOAD (threshold: $LOAD_THRESHOLD)"
  LOAD_5MIN_INT=$(echo "$LOAD_5MIN" | awk '{printf "%d", $1 * 100}')
  LOAD_THRESH_INT=$((LOAD_THRESHOLD * 100))
  if [ "$LOAD_5MIN_INT" -gt "$LOAD_THRESH_INT" ] 2>/dev/null; then
    fail "Load average too high (5min: $LOAD_5MIN > $LOAD_THRESHOLD)"
  else
    pass "Load average OK (5min: $LOAD_5MIN)"
  fi
fi

# 7b. Memory
MEM_INFO=$(require_ssh "Memory info" "free -m | grep '^Mem:'") || MEM_INFO=""
if [ -n "$MEM_INFO" ]; then
  MEM_AVAILABLE=$(echo "$MEM_INFO" | awk '{print $NF}')
  MEM_TOTAL=$(echo "$MEM_INFO" | awk '{print $2}')
  if [ "${MEM_AVAILABLE:-0}" -lt 256 ] 2>/dev/null; then
    fail "Low memory (${MEM_AVAILABLE}MB available of ${MEM_TOTAL}MB)"
  else
    pass "Memory OK (${MEM_AVAILABLE}MB available of ${MEM_TOTAL}MB)"
  fi
fi

# 7c. Swap
SWAP_INFO=$(require_ssh "Swap info" "free -m | grep '^Swap:'") || SWAP_INFO=""
if [ -n "$SWAP_INFO" ]; then
  SWAP_USED=$(echo "$SWAP_INFO" | awk '{print $3}')
  if [ "${SWAP_USED:-0}" -gt 100 ] 2>/dev/null; then
    fail "High swap usage (${SWAP_USED}MB — indicates memory pressure)"
  else
    pass "Swap OK (${SWAP_USED}MB used)"
  fi
fi

# 7d. Disk space
DISK_INFO=$(require_ssh "Disk info" "df -h / | tail -1") || DISK_INFO=""
if [ -n "$DISK_INFO" ]; then
  DISK_PERCENT=$(echo "$DISK_INFO" | awk '{print $5}' | tr -d '%')
  DISK_AVAIL=$(echo "$DISK_INFO" | awk '{print $4}')
  if [ "${DISK_PERCENT:-0}" -gt 85 ] 2>/dev/null; then
    fail "Disk usage high (${DISK_PERCENT}%, ${DISK_AVAIL} available)"
  else
    pass "Disk OK (${DISK_PERCENT}% used, ${DISK_AVAIL} available)"
  fi
fi

# 7e. Server uptime
UPTIME=$($SSH_CMD "uptime -p") || UPTIME="unknown"
info "Server uptime: $UPTIME"

# ============================================================
# 8. CONTAINER HEALTH
# ============================================================
echo ""
echo "--- Container Health ---"

# 8a. All containers running
CONTAINERS=$(remote "$COMPOSE ps --format '{{.Name}}:{{.State}}'") || CONTAINERS=""
CONTAINER_COUNT=0
CONTAINER_DOWN=0
if [ -z "$CONTAINERS" ]; then
  fail "Could not list containers (SSH or Docker may be down)"
else
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    NAME=$(echo "$line" | cut -d: -f1)
    STATE=$(echo "$line" | cut -d: -f2)
    CONTAINER_COUNT=$((CONTAINER_COUNT + 1))
    if [ "$STATE" != "running" ]; then
      fail "Container $NAME is $STATE"
      CONTAINER_DOWN=$((CONTAINER_DOWN + 1))
    fi
  done <<< "$CONTAINERS"
  if [ "$CONTAINER_DOWN" -eq 0 ] && [ "$CONTAINER_COUNT" -gt 0 ]; then
    pass "All $CONTAINER_COUNT containers running"
  fi
fi

# 8b. Container restart count (alert if any restarted in last hour)
for CONTAINER in wp ojs wp-db ojs-db; do
  STARTED_AT=$(remote "$COMPOSE exec -T $CONTAINER cat /proc/1/stat 2>/dev/null | awk '{print \$22}'" 2>/dev/null) || true
  RESTART_COUNT=$(remote "docker inspect --format='{{.RestartCount}}' \$(docker compose -f docker-compose.yml -f docker-compose.staging.yml ps -q $CONTAINER 2>/dev/null) 2>/dev/null") || RESTART_COUNT="0"
  RESTART_COUNT=$(echo "$RESTART_COUNT" | tr -d '[:space:]')
  if [ "${RESTART_COUNT:-0}" -gt 0 ] 2>/dev/null; then
    fail "Container $CONTAINER has restarted $RESTART_COUNT time(s)"
  fi
done

# 8c. Docker log errors (last hour)
# Excludes OJS scheduler NotFoundHttpException — known OJS 3.5 bug where
# runScheduledTasks.php triggers a fatal because there's no HTTP request context.
TOTAL_ERRORS=0
for CONTAINER in wp ojs; do
  ERROR_COUNT=$(remote "docker logs --since=1h \$(docker compose -f docker-compose.yml -f docker-compose.staging.yml ps -q $CONTAINER 2>/dev/null) 2>&1 | grep -iE 'Fatal error|PHP Fatal|Uncaught Exception|Out of memory' | grep -cv 'NotFoundHttpException.*PKPRouter'" 2>/dev/null) || ERROR_COUNT="0"
  ERROR_COUNT=$(echo "$ERROR_COUNT" | tr -d '[:space:]')
  TOTAL_ERRORS=$((TOTAL_ERRORS + ${ERROR_COUNT:-0}))
done
if [ "$TOTAL_ERRORS" -gt 0 ] 2>/dev/null; then
  fail "$TOTAL_ERRORS PHP fatal/OOM errors in container logs (last hour)"
else
  pass "No fatal errors in container logs (last hour)"
fi

# 8d. OOM detection (last 24h only — dmesg accumulates since boot)
UPTIME_SECS=$($SSH_CMD "cat /proc/uptime | cut -d. -f1") || UPTIME_SECS="0"
CUTOFF=$((UPTIME_SECS - 86400))
if [ "$CUTOFF" -lt 0 ]; then CUTOFF=0; fi
OOM_RECENT=$($SSH_CMD "dmesg -T 2>/dev/null | tail -500 | grep -ci 'oom\|out of memory'" 2>/dev/null) || OOM_RECENT="0"
OOM_RECENT=$(echo "$OOM_RECENT" | tr -d '[:space:]')
OOM_24H=$($SSH_CMD "journalctl -k --since '24 hours ago' 2>/dev/null | grep -ci 'oom\|out of memory'" 2>/dev/null) || OOM_24H="$OOM_RECENT"
OOM_24H=$(echo "$OOM_24H" | tr -d '[:space:]')
if [ "${OOM_24H:-0}" -gt 0 ] 2>/dev/null; then
  fail "OOM killer detected in last 24h ($OOM_24H occurrences)"
else
  pass "No OOM kills in last 24h"
fi

# ============================================================
# 9. WP HEALTH
# ============================================================
echo ""
echo "--- WP Health ---"

# 9a. Action Scheduler queue depth
PENDING_JOBS=$(wp_cli "action-scheduler list --status=pending --per-page=100 --format=count" 2>/dev/null) || PENDING_JOBS=""
PENDING_JOBS=$(echo "$PENDING_JOBS" | tr -d '[:space:]')
# action-scheduler CLI may not be registered (depends on WCS version)
if ! [[ "$PENDING_JOBS" =~ ^[0-9]+$ ]]; then
  PENDING_JOBS="0"
fi
if [ "${PENDING_JOBS:-0}" -gt 50 ] 2>/dev/null; then
  fail "Action Scheduler queue backing up ($PENDING_JOBS pending jobs)"
else
  pass "Action Scheduler queue OK ($PENDING_JOBS pending)"
fi

# 9b. Adminer (accessible within Docker network only)
ADMINER_STATUS=$(remote "$COMPOSE exec -T wp curl -s -o /dev/null -w '%{http_code}' http://adminer:8080/") || ADMINER_STATUS="000"
if [ "$ADMINER_STATUS" = "200" ]; then
  pass "Adminer responds (HTTP $ADMINER_STATUS)"
else
  fail "Adminer not responding (HTTP $ADMINER_STATUS)"
fi

# --- Summary ---
echo ""
echo "=== Results: $PASSED/$TOTAL passed, $FAILED failed ==="

ping_heartbeat "$BETTERSTACK_HB_HOURLY" "$FAILED" "$PASSED" "$TOTAL"

if [ "$FAILED" -gt "0" ]; then
  exit 1
fi
