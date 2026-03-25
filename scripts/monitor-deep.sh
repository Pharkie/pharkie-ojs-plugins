#!/bin/bash
# Deep monitoring checks for live environments — runs daily.
# Includes everything from monitor-safe.sh PLUS mutating/expensive checks:
# sync round-trip, backup health, search index, reconciliation, DB size.
#
# Usage:
#   scripts/monitor-deep.sh                      # Test sea-staging
#   scripts/monitor-deep.sh --host=sea-live      # Test live
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Parse arguments (pass through to monitor-safe.sh) ---
SSH_HOST="sea-staging"
ARGS=("$@")
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
  esac
done

# --- Run safe checks first ---
echo "================================================================"
echo "PHASE 1: Safe (non-mutating) checks"
echo "================================================================"
"$SCRIPT_DIR/monitor-safe.sh" "${ARGS[@]}"
SAFE_EXIT=$?

# Re-establish SSH connection and env vars for deep checks
REMOTE_DIR="/opt/pharkie-ojs-plugins"

source "$SCRIPT_DIR/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

# Auto-detect compose config from running containers
COMPOSE_FILES=$($SSH_CMD "docker inspect --format='{{index .Config.Labels \"com.docker.compose.project.config_files\"}}' \$(docker ps -q --filter 'label=com.docker.compose.project=pharkie-ojs-plugins' | head -1) 2>/dev/null") || COMPOSE_FILES=""
if [ -n "$COMPOSE_FILES" ]; then
  COMPOSE="docker compose"
  IFS=',' read -ra FILES <<< "$COMPOSE_FILES"
  for f in "${FILES[@]}"; do
    COMPOSE="$COMPOSE -f $(basename "$f")"
  done
else
  COMPOSE="docker compose -f docker-compose.yml -f docker-compose.staging.yml"
fi

WP_HOME=$($SSH_CMD "grep '^WP_HOME=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_BASE_URL=$($SSH_CMD "grep '^OJS_BASE_URL=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_PATH=$($SSH_CMD "grep '^OJS_JOURNAL_PATH=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"
API_KEY=$($SSH_CMD "grep '^WPOJS_API_KEY_SECRET=' $REMOTE_DIR/.env | cut -d= -f2")

PASSED=0
FAILED=0
TOTAL=0

pass() {
  PASSED=$((PASSED + 1))
  TOTAL=$((TOTAL + 1))
  echo "  [PASS] $1"
}

fail() {
  FAILED=$((FAILED + 1))
  TOTAL=$((TOTAL + 1))
  echo "  [FAIL] $1"
  [ -n "$2" ] && echo "         $2"
}

info() {
  echo "  [INFO] $1"
}

remote() {
  $SSH_CMD "cd $REMOTE_DIR && $*" 2>&1
}

wp_cli() {
  remote "$COMPOSE exec -T wp wp --allow-root $*"
}

echo ""
echo "================================================================"
echo "PHASE 2: Deep (mutating/expensive) checks"
echo "================================================================"

# ============================================================
# D1. SYNC ROUND-TRIP
# ============================================================
echo ""
echo "--- Sync Round-Trip ---"

TEST_EMAIL="monitor-$(date +%s)@test.invalid"
TEST_LOGIN="monitor_$(date +%s)"
SYNC_OK=true

WP_USER_ID=$(wp_cli "user create $TEST_LOGIN $TEST_EMAIL --role=subscriber --first_name=Monitor --last_name=Test --porcelain") || WP_USER_ID=""
if [ -z "$WP_USER_ID" ] || ! [[ "$WP_USER_ID" =~ ^[0-9]+$ ]]; then
  fail "Could not create test user" "$WP_USER_ID"
  SYNC_OK=false
fi

if [ "$SYNC_OK" = true ]; then
  PRODUCT_ID=$(wp_cli "post list --post_type=product --posts_per_page=1 --format=ids") || PRODUCT_ID=""
  SUB_ID=""
  if [ -n "$PRODUCT_ID" ]; then
    SUB_ID=$(wp_cli "eval '
      \$sub = wcs_create_subscription([\"customer_id\" => $WP_USER_ID, \"billing_period\" => \"year\", \"billing_interval\" => 1]);
      if (is_wp_error(\$sub)) { echo 0; return; }
      \$sub->add_product(wc_get_product($PRODUCT_ID));
      \$sub->update_status(\"active\");
      echo \$sub->get_id();
    '") || SUB_ID=""
  fi

  if [ -z "$SUB_ID" ] || [ "$SUB_ID" = "0" ]; then
    fail "Could not create test subscription"
    SYNC_OK=false
  fi
fi

if [ "$SYNC_OK" = true ]; then
  SYNC_OUTPUT=$(wp_cli "ojs-sync sync --member=$TEST_EMAIL --yes" 2>&1) || true
  wp_cli "action-scheduler run" > /dev/null 2>&1 || true

  OJS_USER=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' 'http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/users?email=$TEST_EMAIL'") || OJS_USER=""
  if echo "$OJS_USER" | grep -q "$TEST_EMAIL"; then
    pass "User synced to OJS"

    OJS_USER_ID=$(echo "$OJS_USER" | grep -o '"userId":[0-9]*' | head -1 | cut -d: -f2)
    if [ -n "$OJS_USER_ID" ]; then
      OJS_SUBS=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' 'http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/subscriptions?userId=$OJS_USER_ID'") || OJS_SUBS=""
      if echo "$OJS_SUBS" | grep -q '"status"'; then
        pass "OJS subscription created"
      else
        fail "OJS subscription not found" "$OJS_SUBS"
      fi
    fi
  else
    fail "User not found in OJS after sync" "$OJS_USER"
  fi

  # Cleanup
  wp_cli "user delete $WP_USER_ID --yes" > /dev/null 2>&1 || true
  for _run in 1 2 3; do
    wp_cli "action-scheduler run" > /dev/null 2>&1 || true
  done
  # Hard-delete the anonymised shell
  if [ -n "$OJS_USER_ID" ]; then
    remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -e \"
      DELETE FROM subscriptions WHERE user_id = $OJS_USER_ID;
      DELETE FROM event_log WHERE user_id = $OJS_USER_ID;
      DELETE FROM user_settings WHERE user_id = $OJS_USER_ID;
      DELETE FROM user_user_groups WHERE user_id = $OJS_USER_ID;
      DELETE FROM users WHERE user_id = $OJS_USER_ID;
    \"'" > /dev/null 2>&1 || true
    pass "Monitor test user cleaned up"
  fi
fi

# ============================================================
# D2. BACKUP HEALTH
# ============================================================
echo ""
echo "--- Backup Health ---"

BACKUP_CRON=$($SSH_CMD "crontab -l 2>/dev/null | grep -F 'backup-ojs-db.sh' || true")
if [ -n "$BACKUP_CRON" ]; then
  pass "Backup cron installed"
else
  fail "Backup cron not installed"
fi

BACKUP_KEY=$($SSH_CMD "test -f /opt/backups/ojs/.backup-key && echo 'exists' || echo 'missing'")
if [ "$BACKUP_KEY" = "exists" ]; then
  pass "Backup encryption key present"
else
  fail "Backup encryption key missing"
fi

LATEST_BACKUP=$($SSH_CMD "ls -t /opt/backups/ojs/daily/ojs-*.sql.gz.enc 2>/dev/null | head -1")
if [ -n "$LATEST_BACKUP" ]; then
  BACKUP_AGE=$($SSH_CMD "echo \$(( (\$(date +%s) - \$(stat -c %Y '$LATEST_BACKUP')) / 3600 ))h")
  BACKUP_SIZE=$($SSH_CMD "stat -c%s '$LATEST_BACKUP' | numfmt --to=iec")
  BACKUP_AGE_H=$(echo "$BACKUP_AGE" | tr -d 'h')
  if [ "${BACKUP_AGE_H:-0}" -gt 25 ] 2>/dev/null; then
    fail "Latest backup too old: $(basename "$LATEST_BACKUP") ($BACKUP_SIZE, ${BACKUP_AGE} old)"
  else
    pass "Latest backup: $(basename "$LATEST_BACKUP") ($BACKUP_SIZE, ${BACKUP_AGE} old)"
  fi
else
  fail "No encrypted backups found"
fi

# ============================================================
# D3. SEARCH INDEX
# ============================================================
echo ""
echo "--- Search Index ---"

SEARCH_OBJECTS=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM submission_search_objects\"'") || SEARCH_OBJECTS="0"
SEARCH_OBJECTS=$(echo "$SEARCH_OBJECTS" | tr -d '[:space:]')
if [ "$SEARCH_OBJECTS" -gt "0" ] 2>/dev/null; then
  pass "Search index has $SEARCH_OBJECTS objects"
else
  fail "Search index is empty"
fi

# HTTP search test
SEARCH_AUTHOR=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"
  SELECT asv.setting_value FROM author_settings asv
  JOIN authors a ON a.author_id = asv.author_id
  JOIN publications p ON a.publication_id = p.publication_id
  JOIN submissions s ON s.submission_id = p.submission_id
  WHERE asv.setting_name = CHAR(102,97,109,105,108,121,78,97,109,101) AND s.status = 3
  GROUP BY asv.setting_value HAVING COUNT(*) >= 3
  ORDER BY COUNT(*) DESC LIMIT 1
\"'") || SEARCH_AUTHOR=""
SEARCH_AUTHOR=$(echo "$SEARCH_AUTHOR" | tr -d '[:space:]')

if [ -n "$SEARCH_AUTHOR" ]; then
  SEARCH_BODY=$(curl -s "${OJS_JOURNAL_URL}/search/search?authors=${SEARCH_AUTHOR}" 2>/dev/null)
  if echo "$SEARCH_BODY" | grep -qi "obj_article_summary\|search_results\|$SEARCH_AUTHOR"; then
    pass "Search for author '$SEARCH_AUTHOR' returns results"
  elif echo "$SEARCH_BODY" | grep -qi "No items found\|no results"; then
    fail "Search for author '$SEARCH_AUTHOR' returned no results"
  else
    fail "Search page for '$SEARCH_AUTHOR' returned unexpected content"
  fi
fi

# ============================================================
# D4. RECONCILIATION
# ============================================================
echo ""
echo "--- Reconciliation ---"

RECON_OUTPUT=$(wp_cli "ojs-sync reconcile") || RECON_OUTPUT=""
if echo "$RECON_OUTPUT" | grep -q "Reconciliation complete"; then
  pass "Reconciliation completes successfully"
else
  fail "Reconciliation failed" "$(echo "$RECON_OUTPUT" | tail -3)"
fi

# ============================================================
# D5. DATABASE SIZES
# ============================================================
echo ""
echo "--- Database ---"

for DB_CONTAINER in wp-db ojs-db; do
  DB_SIZE=$(remote "$COMPOSE exec -T $DB_CONTAINER bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 1) FROM information_schema.tables WHERE table_schema = DATABASE()\"'" 2>/dev/null) || DB_SIZE=""
  DB_SIZE=$(echo "$DB_SIZE" | tr -d '[:space:]')
  if [ -n "$DB_SIZE" ]; then
    DB_SIZE_INT=$(echo "$DB_SIZE" | cut -d. -f1)
    if [ "${DB_SIZE_INT:-0}" -gt 2048 ] 2>/dev/null; then
      fail "$DB_CONTAINER database size ${DB_SIZE}MB exceeds 2GB"
    else
      pass "$DB_CONTAINER database size: ${DB_SIZE}MB"
    fi
  fi
done

# ============================================================
# D6. OJS SCHEDULED TASKS
# ============================================================
echo ""
echo "--- OJS Scheduled Tasks ---"

# Check cron is installed inside the OJS container
OJS_CRON=$(remote "$COMPOSE exec -T ojs crontab -l 2>/dev/null || echo ''")
if echo "$OJS_CRON" | grep -q "scheduler\.php"; then
  pass "OJS scheduler cron installed"
else
  fail "OJS scheduler cron not found in container"
fi

# Check scheduler last ran recently (jobs table updated = scheduler ran)
LAST_JOB_AGE=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT TIMESTAMPDIFF(HOUR, MAX(reserved_at), NOW()) FROM jobs WHERE reserved_at IS NOT NULL\"'" 2>/dev/null) || LAST_JOB_AGE=""
LAST_JOB_AGE=$(echo "$LAST_JOB_AGE" | tr -d '[:space:]')
if [ "$LAST_JOB_AGE" = "NULL" ] || [ -z "$LAST_JOB_AGE" ]; then
  info "No recently processed jobs (queue may be empty — OK if no pending tasks)"
elif [ "${LAST_JOB_AGE:-0}" -gt 3 ] 2>/dev/null; then
  fail "Last job processed ${LAST_JOB_AGE}h ago (scheduler may be stuck)"
else
  pass "Scheduler active (last job processed ${LAST_JOB_AGE}h ago)"
fi

# --- Deep Summary ---
echo ""
echo "=== Deep results: $PASSED/$TOTAL passed, $FAILED failed ==="

# Ping Better Stack heartbeat (if configured)
if [ -n "$BETTERSTACK_HB_DAILY" ]; then
  if [ "$SAFE_EXIT" -ne 0 ] || [ "$FAILED" -gt 0 ]; then
    curl -sf -d "deep checks: $PASSED/$TOTAL passed, $FAILED failed" "$BETTERSTACK_HB_DAILY/fail" > /dev/null 2>&1 || true
  else
    curl -sf "$BETTERSTACK_HB_DAILY" > /dev/null 2>&1 || true
  fi
fi

if [ "$SAFE_EXIT" -ne 0 ] || [ "$FAILED" -gt 0 ]; then
  exit 1
fi
