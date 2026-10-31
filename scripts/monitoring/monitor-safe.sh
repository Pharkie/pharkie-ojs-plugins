#!/bin/bash
# Non-mutating monitoring checks for the journal (OJS) and the box it runs on.
# Runs FROM the devcontainer, GitHub Actions, server cron, or any machine with SSH access.
# Does NOT create test users or modify any state on the server. Needs no
# WordPress: the sync API is checked from inside the ojs container.
#
# Usage:
#   scripts/monitoring/monitor-safe.sh                      # Test sea-staging (via SSH)
#   scripts/monitoring/monitor-safe.sh --host=sea-live      # Test live (via SSH)
#   scripts/monitoring/monitor-safe.sh --host=local         # Run locally (no SSH); use from cron on the host
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

if [ "$SSH_HOST" = "local" ]; then
  # Local mode: skip SSH, run commands directly via bash -c.
  # ssh_retry "$@" runs $@ literally, so SSH_CMD="bash -c" makes
  # `ssh_retry $SSH_CMD "cmd"` resolve to `bash -c "cmd"`.
  SSH_CMD="bash -c"
  SERVER_IP="local"
else
  source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
  resolve_ssh "$SSH_HOST"
fi

source "$SCRIPTS_ROOT/lib/monitor-helpers.sh"

# Register heartbeat so exit trap can ping it on crash
register_heartbeat "$BETTERSTACK_HB_HOURLY"

# --- Initialise: read env, detect compose ---
detect_compose

# Required env vars — fail gracefully if SSH is broken
OJS_BASE_URL=$(require_env "OJS_BASE_URL") || OJS_BASE_URL=""
OJS_JOURNAL_PATH=$(require_env "OJS_JOURNAL_PATH") || OJS_JOURNAL_PATH=""
API_KEY=$(require_env "WPOJS_API_KEY_SECRET") || API_KEY=""

if [ -z "$OJS_BASE_URL" ] || [ -z "$OJS_JOURNAL_PATH" ] || [ -z "$API_KEY" ]; then
  echo ""
  echo "=== Results: $PASSED/$TOTAL passed, $FAILED failed ==="
  echo "FATAL: Could not read required env vars — SSH may be broken"
  ping_heartbeat "$BETTERSTACK_HB_HOURLY" "$FAILED" "$PASSED" "$TOTAL"
  exit 1
fi
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"

echo "=== Monitor (safe): $SSH_HOST ==="
echo "    OJS: $OJS_BASE_URL"
echo ""

# ============================================================
# 1. HTTP & API CHECKS
# ============================================================
echo "--- HTTP & API ---"

# 1a. OJS HTTP
OJS_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$OJS_BASE_URL" 2>/dev/null) || OJS_STATUS="000"
OJS_TIME=$(curl -s -o /dev/null -w '%{time_total}' "$OJS_BASE_URL" 2>/dev/null) || OJS_TIME="0"
if [ "$OJS_STATUS" = "200" ] || [ "$OJS_STATUS" = "301" ] || [ "$OJS_STATUS" = "302" ]; then
  pass "OJS responds (HTTP $OJS_STATUS, ${OJS_TIME}s)"
else
  fail "OJS not responding (HTTP $OJS_STATUS)"
fi

# 1b. OJS Admin
OJS_ADMIN_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$OJS_JOURNAL_URL/management/settings/access" 2>/dev/null) || OJS_ADMIN_STATUS="000"
OJS_ADMIN_BODY=$(curl -s "$OJS_JOURNAL_URL/management/settings/access" 2>/dev/null | head -30)
if echo "$OJS_ADMIN_BODY" | grep -qi "Fatal error\|Exception\|404 Not Found"; then
  fail "OJS Admin page error (HTTP $OJS_ADMIN_STATUS)"
elif [ "$OJS_ADMIN_STATUS" = "200" ] || [ "$OJS_ADMIN_STATUS" = "302" ]; then
  pass "OJS Admin responds (HTTP $OJS_ADMIN_STATUS)"
else
  fail "OJS Admin not responding (HTTP $OJS_ADMIN_STATUS)"
fi

# 1c. Response time threshold (10s — generous for cross-Atlantic CI runners)
OJS_TIME_INT=$(echo "$OJS_TIME" | cut -d. -f1)
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

# 2b. OJS preflight. The keyed calls go to http://ojs:80 from inside the ojs
# container, not to localhost: the API's IP allowlist (WPOJS_ALLOWED_IPS) admits
# the Docker network, and the container's own address on it is in that range.
PREFLIGHT=$(remote "$COMPOSE exec -T ojs curl -sf -H 'Authorization: Bearer $API_KEY' http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/preflight") || PREFLIGHT=""
if echo "$PREFLIGHT" | grep -q '"compatible":true'; then
  CHECKS=$(echo "$PREFLIGHT" | grep -o '"ok":true' | wc -l)
  pass "Preflight passes ($CHECKS checks OK)"
else
  fail "Preflight failed" "${PREFLIGHT:-<empty — SSH or container may be down>}"
fi

# ============================================================
# 4. SUBSCRIPTION TYPES
# ============================================================
echo ""
echo "--- Subscription Config ---"

SUB_TYPES=$(remote "$COMPOSE exec -T ojs curl -sf -H 'Authorization: Bearer $API_KEY' http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/subscription-types") || SUB_TYPES=""
TYPE_COUNT=$(echo "$SUB_TYPES" | grep -o '"id"' | wc -l)
if [ "$TYPE_COUNT" -gt "0" ]; then
  pass "$TYPE_COUNT subscription type(s) configured"
else
  fail "No subscription types found" "${SUB_TYPES:-<empty — SSH or API may be down>}"
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

# 6c. Failed jobs — separate critical (sync, payment) from benign (stats compilation).
# OJS statistics jobs (CompileMonthlyMetrics etc.) fail after reimports due to FK
# constraint issues on orphaned metrics rows. These aren't business-critical.
FAILED_CRITICAL=$(require_remote "OJS critical failed jobs" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM failed_jobs WHERE payload NOT LIKE \\\"%statistics%\\\"\"'") || FAILED_CRITICAL=""
FAILED_STATS=$(require_remote "OJS stats failed jobs" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM failed_jobs WHERE payload LIKE \\\"%statistics%\\\"\"'") || FAILED_STATS=""
if [ -n "$FAILED_CRITICAL" ]; then
  if [ "${FAILED_CRITICAL:-0}" -gt 0 ] 2>/dev/null; then
    fail "$FAILED_CRITICAL failed OJS jobs (check failed_jobs table)"
  else
    pass "No critical failed OJS jobs"
  fi
fi
if [ "${FAILED_STATS:-0}" -gt 0 ] 2>/dev/null; then
  warn "$FAILED_STATS failed OJS statistics jobs (non-critical, will retry next month)"
fi

# ============================================================
# 7. SERVER RESOURCES
# ============================================================
echo ""
echo "--- Server Resources ---"

# 7a. Load average
# NB: ssh_retry, NOT require_ssh — require_ssh strips whitespace (tr -d
# '[:space:]'), collapsing /proc/loadavg into one token and breaking the awk
# field parse. Same root cause as the disk check below; fail loud if unreadable.
LOAD=$(ssh_retry $SSH_CMD "cat /proc/loadavg") || LOAD=""
LOAD_5MIN=$(echo "$LOAD" | awk '{print $2}')
NPROC=$(ssh_retry $SSH_CMD "nproc") || NPROC="3"
LOAD_THRESHOLD=$(( NPROC * 2 ))
info "Load average: $LOAD (threshold: $LOAD_THRESHOLD)"
LOAD_5MIN_INT=$(echo "$LOAD_5MIN" | awk '{printf "%d", $1 * 100}')
LOAD_THRESH_INT=$((LOAD_THRESHOLD * 100))
if ! [[ "$LOAD_5MIN" =~ ^[0-9]+\.?[0-9]*$ ]]; then
  fail "Load average unreadable (/proc/loadavg: ${LOAD:-<empty>})"
elif [ "$LOAD_5MIN_INT" -gt "$LOAD_THRESH_INT" ]; then
  fail "Load average too high (5min: $LOAD_5MIN > $LOAD_THRESHOLD)"
else
  pass "Load average OK (5min: $LOAD_5MIN)"
fi

# 7b. Memory
MEM_INFO=$(ssh_retry $SSH_CMD "free -m | grep '^Mem:'") || MEM_INFO=""
MEM_AVAILABLE=$(echo "$MEM_INFO" | awk '{print $NF}')
MEM_TOTAL=$(echo "$MEM_INFO" | awk '{print $2}')
if ! [[ "$MEM_AVAILABLE" =~ ^[0-9]+$ ]]; then
  fail "Memory info unreadable (free -m: ${MEM_INFO:-<empty>})"
elif [ "$MEM_AVAILABLE" -lt 256 ]; then
  fail "Low memory (${MEM_AVAILABLE}MB available of ${MEM_TOTAL}MB)"
else
  pass "Memory OK (${MEM_AVAILABLE}MB available of ${MEM_TOTAL}MB)"
fi

# 7c. Swap and memory pressure
# Swap is REPORTED, never alerted on. Memory pressure is measured directly.
#
# How full swap is says nothing about trouble. The 03:00 backups push cold pages
# of the long-lived daemons (mysqld, mariadbd, vector, node) out to the 3GB
# swapfile, and nothing reads them back. A "more than half full" alert then
# stayed red for hours on a healthy box (3 and 18 Oct 2026: si/so 0, ~2GB of
# RAM available); the >100MB alert before it fired at load 0.2. Parked pages
# are the swapfile doing its job.
#
# /proc/pressure/memory measures the thing itself: the share of time tasks were
# STALLED waiting on memory. `full` is the share when EVERY task was stalled;
# a healthy box reads 0.00, a thrashing one whole percentages and climbing.
# avg300 (five minutes) rides out a momentary spike during a deploy.
SWAP_INFO=$(ssh_retry $SSH_CMD "free -m | grep '^Swap:'") || SWAP_INFO=""
SWAP_USED=$(echo "$SWAP_INFO" | awk '{print $3}')
SWAP_TOTAL=$(echo "$SWAP_INFO" | awk '{print $2}')
info "Swap: ${SWAP_USED:-?}MB of ${SWAP_TOTAL:-?}MB used (diagnostic only — see the pressure check)"

# Tenths of a percent of time fully stalled: fail at 0.5%, warn at 0.1%.
PSI_FAIL="${MONITOR_MEM_PSI_FAIL:-5}"
PSI_WARN="${MONITOR_MEM_PSI_WARN:-1}"
[[ "$PSI_FAIL" =~ ^[0-9]+$ ]] || { warn "MONITOR_MEM_PSI_FAIL='$PSI_FAIL' is not a whole number — using 5"; PSI_FAIL=5; }
[[ "$PSI_WARN" =~ ^[0-9]+$ ]] || { warn "MONITOR_MEM_PSI_WARN='$PSI_WARN' is not a whole number — using 1"; PSI_WARN=1; }
if ! PSI_LINE=$(ssh_retry $SSH_CMD "cat /proc/pressure/memory 2>/dev/null | grep '^full' || true"); then
  fail "Memory pressure unreadable (${PSI_LINE:-<empty>})"
else
  PSI_AVG300=$(echo "$PSI_LINE" | grep -oE 'avg300=[0-9]+(\.[0-9]+)?' | cut -d= -f2)
  if [ -z "$PSI_AVG300" ]; then
    # Not fatal: PSI needs CONFIG_PSI, which some kernels lack. The available-RAM
    # check (7b) has already judged memory, so fall back on it.
    warn "No /proc/pressure/memory on this kernel — relying on the available-RAM check alone"
    pass "Memory pressure not measurable (PSI unavailable)"
  else
    # Integer tenths, so this compares without bc: "0.42" -> 4.
    PSI_TENTHS=$(echo "$PSI_AVG300" | awk '{printf "%d", $1 * 10}')
    if [ "$PSI_TENTHS" -ge "$PSI_FAIL" ]; then
      fail "Memory pressure: every task stalled on memory ${PSI_AVG300}% of the last 5 minutes (swap ${SWAP_USED:-?}MB, ${MEM_AVAILABLE:-?}MB RAM available)"
    elif [ "$PSI_TENTHS" -ge "$PSI_WARN" ]; then
      warn "Some memory pressure (full avg300=${PSI_AVG300}%, swap ${SWAP_USED:-?}MB)"
      pass "Memory pressure below the alert threshold (${PSI_AVG300}%)"
    else
      pass "No memory pressure (full avg300=${PSI_AVG300}%)"
    fi
  fi
fi

# 7d. Disk space
# NB: call ssh_retry directly, NOT require_ssh — require_ssh runs the result
# through `tr -d '[:space:]'`, which collapses df's whitespace-delimited columns
# into a single token. That made the awk field parse below return empty, so
# DISK_PERCENT defaulted to 0 and this check passed at ANY usage. The disk
# silently filled to 100% on 2026-05-24 with no alert because of this.
DISK_INFO=$(ssh_retry $SSH_CMD "df -h / | tail -1") || DISK_INFO=""
DISK_PERCENT=$(echo "$DISK_INFO" | awk '{print $5}' | tr -d '%')
DISK_AVAIL=$(echo "$DISK_INFO" | awk '{print $4}')
if ! [[ "$DISK_PERCENT" =~ ^[0-9]+$ ]]; then
  fail "Disk usage unreadable (df returned: ${DISK_INFO:-<empty>})"
elif [ "$DISK_PERCENT" -gt 85 ]; then
  fail "Disk usage high (${DISK_PERCENT}%, ${DISK_AVAIL} available)"
else
  pass "Disk OK (${DISK_PERCENT}% used, ${DISK_AVAIL} available)"
fi

# 7e. Server uptime
UPTIME=$(ssh_retry $SSH_CMD "uptime -p") || UPTIME="unknown"
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

# 8b. Container restart count (info only — RestartCount accumulates until
# next docker compose up, so a single crash-restart would cause permanent
# hourly alerts. Crash-restarts are already caught by OOM detection and
# PHP fatal error checks.)
for CONTAINER in ojs ojs-db; do
  RESTART_COUNT=$(remote "docker inspect --format='{{.RestartCount}}' \$($COMPOSE ps -q $CONTAINER 2>/dev/null) 2>/dev/null") || RESTART_COUNT="0"
  RESTART_COUNT=$(echo "$RESTART_COUNT" | tr -d '[:space:]')
  if [ "${RESTART_COUNT:-0}" -gt 0 ] 2>/dev/null; then
    info "Container $CONTAINER has restarted $RESTART_COUNT time(s) since last deploy"
  fi
done

# 8c. Docker log errors (last hour)
# Excludes known OJS 3.5 framework bugs (see docs/ojs-issues-log.md #22, #24, #27):
#   - NotFoundHttpException: runScheduledTasks.php has no HTTP request context
#   - flock()/LockableFile: transient cache lock race condition
#   - Core::cleanFileVar: TypeError on non-UTF-8 request paths (scanner traffic)
#   - IssueHandler getId() on null: malformed issue/galley URLs from bots (#28)
# Threshold: 1-2 errors = WARN (transient), 3+ = FAIL (persistent problem)
PHP_ERROR_THRESHOLD=3
PHP_ERROR_EXCLUDE='NotFoundHttpException.*PKPRouter|flock\(\).*LockableFile|Core::cleanFileVar|getId\(\) on null in.*IssueHandler'
TOTAL_ERRORS=0
ERROR_SAMPLE=""
CONTAINER_ID=$(remote "$COMPOSE ps -q ojs 2>/dev/null" 2>/dev/null) || CONTAINER_ID=""
if [ -n "$CONTAINER_ID" ]; then
  ERROR_COUNT=$(remote "docker logs --since=1h $CONTAINER_ID 2>&1 | grep -iE 'Fatal error|PHP Fatal|Uncaught Exception|Out of memory' | grep -vcE '$PHP_ERROR_EXCLUDE'" 2>/dev/null) || ERROR_COUNT="0"
  ERROR_COUNT=$(echo "$ERROR_COUNT" | tr -d '[:space:]')
  TOTAL_ERRORS=$((TOTAL_ERRORS + ${ERROR_COUNT:-0}))
  # Capture the last error line for diagnostics
  if [ "${ERROR_COUNT:-0}" -gt 0 ] 2>/dev/null; then
    SAMPLE=$(remote "docker logs --since=1h $CONTAINER_ID 2>&1 | grep -iE 'Fatal error|PHP Fatal|Uncaught Exception|Out of memory' | grep -vE '$PHP_ERROR_EXCLUDE' | tail -1 | cut -c1-120" 2>/dev/null) || SAMPLE=""
    [ -n "$SAMPLE" ] && ERROR_SAMPLE="[ojs] $SAMPLE"
  fi
fi
if [ "$TOTAL_ERRORS" -ge "$PHP_ERROR_THRESHOLD" ] 2>/dev/null; then
  fail "$TOTAL_ERRORS PHP fatal/OOM errors in container logs (last hour)" "$ERROR_SAMPLE"
elif [ "$TOTAL_ERRORS" -gt 0 ] 2>/dev/null; then
  warn "$TOTAL_ERRORS PHP fatal/OOM error(s) in container logs (last hour, below threshold of $PHP_ERROR_THRESHOLD)" "$ERROR_SAMPLE"
  pass "PHP errors below alert threshold ($TOTAL_ERRORS < $PHP_ERROR_THRESHOLD)"
else
  pass "No fatal errors in container logs (last hour)"
fi

# 8d. OOM detection (last 24h only — dmesg accumulates since boot)
UPTIME_SECS=$(ssh_retry $SSH_CMD "cat /proc/uptime | cut -d. -f1") || UPTIME_SECS="0"
CUTOFF=$((UPTIME_SECS - 86400))
if [ "$CUTOFF" -lt 0 ]; then CUTOFF=0; fi
OOM_RECENT=$(ssh_retry $SSH_CMD "dmesg -T 2>/dev/null | tail -500 | grep -ciE '\boom\b|out of memory|oom-killer'" 2>/dev/null) || OOM_RECENT="0"
OOM_RECENT=$(echo "$OOM_RECENT" | tr -d '[:space:]')
OOM_24H=$(ssh_retry $SSH_CMD "journalctl -k --since '24 hours ago' 2>/dev/null | grep -ciE '\boom\b|out of memory|oom-killer'" 2>/dev/null) || OOM_24H="$OOM_RECENT"
OOM_24H=$(echo "$OOM_24H" | tr -d '[:space:]')
if [ "${OOM_24H:-0}" -gt 0 ] 2>/dev/null; then
  fail "OOM killer detected in last 24h ($OOM_24H occurrences)"
else
  pass "No OOM kills in last 24h"
fi

# ============================================================
# 9. ADMINER
# ============================================================
echo ""
echo "--- Adminer ---"

# 9a. Adminer (accessible within Docker network only, bound to 127.0.0.1)
ADMINER_STATUS=$(remote "$COMPOSE exec -T ojs curl -s -o /dev/null -w '%{http_code}' http://adminer:8080/") || ADMINER_STATUS="000"
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
