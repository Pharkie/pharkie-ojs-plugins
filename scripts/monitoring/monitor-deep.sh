#!/bin/bash
# Deep monitoring checks for live environments — runs daily.
# Includes everything from monitor-safe.sh PLUS mutating/expensive checks:
# sync round-trip, backup health, search index, reconciliation, DB size.
#
# Usage:
#   scripts/monitoring/monitor-deep.sh                      # Test sea-staging
#   scripts/monitoring/monitor-deep.sh --host=sea-live      # Test live
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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
source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

source "$SCRIPTS_ROOT/lib/monitor-helpers.sh"

# Register heartbeat so exit trap can ping it on crash
register_heartbeat "$BETTERSTACK_HB_DAILY"

detect_compose

WP_HOME=$(read_env "WP_HOME")
OJS_BASE_URL=$(require_env "OJS_BASE_URL") || OJS_BASE_URL=""
OJS_JOURNAL_PATH=$(require_env "OJS_JOURNAL_PATH") || OJS_JOURNAL_PATH=""
API_KEY=$(require_env "WPOJS_API_KEY_SECRET") || API_KEY=""

if [ -z "$OJS_BASE_URL" ] || [ -z "$OJS_JOURNAL_PATH" ] || [ -z "$API_KEY" ]; then
  echo ""
  echo "=== Deep results: $PASSED/$TOTAL passed, $FAILED failed ==="
  echo "FATAL: Could not read required env vars — SSH may be broken"
  ping_heartbeat "$BETTERSTACK_HB_DAILY" "$((FAILED + (SAFE_EXIT > 0 ? 1 : 0)))" "$PASSED" "$TOTAL"
  exit 1
fi
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"

echo ""
echo "================================================================"
echo "PHASE 2: Deep (mutating/expensive) checks"
echo "================================================================"

# ============================================================
# D1. SYNC ROUND-TRIP
# ============================================================
echo ""
echo "--- Sync Round-Trip ---"

# Cleanup stale monitor users from previous failed runs
STALE_IDS=$(wp_cli "user list --search=monitor-*@test.invalid --field=ID" 2>/dev/null) || STALE_IDS=""
if [ -n "$STALE_IDS" ]; then
  echo "$STALE_IDS" | while read uid; do
    [ -z "$uid" ] && continue
    wp_cli "user delete $uid --yes" > /dev/null 2>&1 || true
  done
  info "Cleaned up $(echo "$STALE_IDS" | wc -w) stale monitor user(s)"
fi

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
        fail "OJS subscription not found" "${OJS_SUBS:-<empty>}"
      fi
    fi
  else
    fail "User not found in OJS after sync" "${OJS_USER:-<empty>}"
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

# Single SSH call: read crontab + verify script exists.
# Must start with "sudo crontab -l" to match the monitor-shell.sh allowlist
# (the CI key is forced through that wrapper). Keep this on one line.
BACKUP_CHECK=$(ssh_retry $SSH_CMD "sudo crontab -l 2>/dev/null | grep -oP '/\S+backup-ojs-db\.sh' | head -1 | { read -r SCRIPT || true; if [ -z \"\$SCRIPT\" ]; then echo no_cron; elif [ -f \"\$SCRIPT\" ]; then echo \"script_ok:\$SCRIPT\"; else echo \"script_missing:\$SCRIPT\"; fi; }") || BACKUP_CHECK="ssh_failed"
case "$BACKUP_CHECK" in
  script_ok:*)  pass "Backup cron installed ($(basename "${BACKUP_CHECK#script_ok:}"))" ;;
  script_missing:*) fail "Backup cron points to missing script: ${BACKUP_CHECK#script_missing:}" ;;
  no_cron)      fail "Backup cron not installed" ;;
  *)            fail "Could not verify backup cron (SSH: $BACKUP_CHECK)" ;;
esac

BACKUP_KEY=$(ssh_retry $SSH_CMD "test -f /opt/backups/ojs/.backup-key && echo 'exists' || echo 'missing'")
if [ "$BACKUP_KEY" = "exists" ]; then
  pass "Backup encryption key present"
else
  fail "Backup encryption key missing"
fi

LATEST_BACKUP=$(ssh_retry $SSH_CMD "ls -t /opt/backups/ojs/daily/ojs-*.sql.gz.enc 2>/dev/null | head -1")
if [ -n "$LATEST_BACKUP" ]; then
  BACKUP_AGE=$(ssh_retry $SSH_CMD "echo \$(( (\$(date +%s) - \$(stat -c %Y '$LATEST_BACKUP')) / 3600 ))h")
  BACKUP_SIZE=$(ssh_retry $SSH_CMD "stat -c%s '$LATEST_BACKUP' | numfmt --to=iec")
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

# Coverage check: proportion of published submissions that have search-index rows.
# Fails the common regression where rebuildSearchIndex.php is run but jobs are
# cleared before they process (commit 82ba72d on pipe7_import.sh), which left
# the live site with only the 2 most recent issues searchable in 2026-04.
# Thresholds: fail if >20 submissions missing OR coverage <90%; warn at 5+ missing.
INDEXED=$(require_remote "Search index distinct submissions" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(DISTINCT submission_id) FROM submission_search_objects\"'") || INDEXED=""
PUBLISHED=$(require_remote "Published submissions" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM submissions WHERE status = 3 AND current_publication_id IS NOT NULL\"'") || PUBLISHED=""

if [ -n "$INDEXED" ] && [ -n "$PUBLISHED" ] && [ "$PUBLISHED" -gt 0 ] 2>/dev/null; then
  MISSING=$((PUBLISHED - INDEXED))
  PCT=$((INDEXED * 100 / PUBLISHED))
  if [ "$MISSING" -gt 20 ] || [ "$PCT" -lt 90 ]; then
    fail "Search index coverage: $INDEXED/$PUBLISHED submissions indexed (${PCT}%, $MISSING missing)"
  elif [ "$MISSING" -gt 5 ]; then
    warn "Search index partial: $INDEXED/$PUBLISHED submissions indexed ($MISSING missing)"
  else
    pass "Search index: $INDEXED/$PUBLISHED submissions indexed (${PCT}%)"
  fi
fi

# HTTP search test — pick one author from the oldest 10% of submissions and one
# from the newest 10%. Fail if either returns no results. Catches "old articles
# missing" (the 2026-04 bug signature) distinctly from "everything broken".
# CHAR(102,97,109,105,108,121,78,97,109,101) = 'familyName'
test_search_author() {
  local label="$1" sql="$2"
  local author
  author=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"$sql\"'") || author=""
  author=$(echo "$author" | tr -d '[:space:]')
  if [ -z "$author" ]; then
    info "No $label author found for HTTP search test (skipping)"
    return
  fi
  local body
  body=$(curl -s "${OJS_JOURNAL_URL}/search/search?authors=${author}" 2>/dev/null)
  if echo "$body" | grep -qi "obj_article_summary\|search_results\|$author"; then
    pass "Search for $label author '$author' returns results"
  elif echo "$body" | grep -qi "No items found\|no results"; then
    fail "Search for $label author '$author' returned no results"
  else
    fail "Search page for $label author '$author' returned unexpected content"
  fi
}

OLDEST_AUTHOR_SQL="
  SELECT asv.setting_value FROM author_settings asv
  JOIN authors a ON a.author_id = asv.author_id
  JOIN publications p ON a.publication_id = p.publication_id
  JOIN submissions s ON s.submission_id = p.submission_id
  WHERE asv.setting_name = CHAR(102,97,109,105,108,121,78,97,109,101) AND s.status = 3
    AND s.submission_id IN (
      SELECT submission_id FROM (
        SELECT submission_id FROM submissions WHERE status = 3
        ORDER BY submission_id ASC LIMIT 200
      ) t
    )
  GROUP BY asv.setting_value HAVING COUNT(*) >= 2
  ORDER BY COUNT(*) DESC LIMIT 1
"
NEWEST_AUTHOR_SQL="
  SELECT asv.setting_value FROM author_settings asv
  JOIN authors a ON a.author_id = asv.author_id
  JOIN publications p ON a.publication_id = p.publication_id
  JOIN submissions s ON s.submission_id = p.submission_id
  WHERE asv.setting_name = CHAR(102,97,109,105,108,121,78,97,109,101) AND s.status = 3
    AND s.submission_id IN (
      SELECT submission_id FROM (
        SELECT submission_id FROM submissions WHERE status = 3
        ORDER BY submission_id DESC LIMIT 200
      ) t
    )
  GROUP BY asv.setting_value HAVING COUNT(*) >= 2
  ORDER BY COUNT(*) DESC LIMIT 1
"

test_search_author "oldest" "$OLDEST_AUTHOR_SQL"
test_search_author "newest" "$NEWEST_AUTHOR_SQL"

# ============================================================
# D4. RECONCILIATION
# ============================================================
echo ""
echo "--- Reconciliation ---"

RECON_OUTPUT=$(wp_cli "ojs-sync reconcile") || RECON_OUTPUT=""
if echo "$RECON_OUTPUT" | grep -q "Reconciliation complete"; then
  pass "Reconciliation completes successfully"
else
  fail "Reconciliation failed" "$(echo "${RECON_OUTPUT:-<empty>}" | tail -3)"
fi

# ============================================================
# D5. DATABASE SIZES
# ============================================================
echo ""
echo "--- Database ---"

for DB_CONTAINER in wp-db ojs-db; do
  DB_SIZE=$(require_remote "$DB_CONTAINER database size" "$COMPOSE exec -T $DB_CONTAINER bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 1) FROM information_schema.tables WHERE table_schema = DATABASE()\"'") || DB_SIZE=""
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

OJS_CRON=$(remote "$COMPOSE exec -T ojs crontab -l 2>/dev/null || echo ''")
if echo "$OJS_CRON" | grep -q "scheduler\.php\|ojs-scheduler-heartbeat"; then
  pass "OJS scheduler cron installed"
else
  fail "OJS scheduler cron not found in container"
fi

# Check scheduler last ran recently (jobs table updated = scheduler ran).
# Cross-check with pending count: if nothing is pending, an old last-processed
# time is expected and not a problem.
LAST_JOB_AGE=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT TIMESTAMPDIFF(HOUR, MAX(reserved_at), NOW()) FROM jobs WHERE reserved_at IS NOT NULL\"'" 2>/dev/null) || LAST_JOB_AGE=""
LAST_JOB_AGE=$(echo "$LAST_JOB_AGE" | tr -d '[:space:]')
PENDING_SCHED=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM jobs WHERE reserved_at IS NULL\"'" 2>/dev/null) || PENDING_SCHED="0"
PENDING_SCHED=$(echo "$PENDING_SCHED" | tr -d '[:space:]')
if [ "$LAST_JOB_AGE" = "NULL" ] || [ -z "$LAST_JOB_AGE" ]; then
  info "No recently processed jobs (queue may be empty — OK if no pending tasks)"
elif [ "${LAST_JOB_AGE:-0}" -gt 3 ] 2>/dev/null; then
  if [ "${PENDING_SCHED:-0}" -gt 0 ] 2>/dev/null; then
    fail "Last job processed ${LAST_JOB_AGE}h ago with $PENDING_SCHED pending (scheduler may be stuck)"
  else
    pass "Scheduler idle (last job ${LAST_JOB_AGE}h ago, queue empty)"
  fi
else
  pass "Scheduler active (last job processed ${LAST_JOB_AGE}h ago)"
fi

# --- Deep Summary ---
echo ""
echo "=== Deep results: $PASSED/$TOTAL passed, $FAILED failed ==="

ping_heartbeat "$BETTERSTACK_HB_DAILY" "$((FAILED + (SAFE_EXIT > 0 ? 1 : 0)))" "$PASSED" "$TOTAL"

if [ "$SAFE_EXIT" -ne 0 ] || [ "$FAILED" -gt 0 ]; then
  exit 1
fi
