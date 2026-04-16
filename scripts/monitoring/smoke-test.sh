#!/bin/bash
# Lightweight smoke tests for staging/production environments.
# Runs FROM the devcontainer (or any machine with SSH access).
# No Node, no Playwright — just curl + WP-CLI via SSH.
#
# Usage:
#   scripts/monitoring/smoke-test.sh                      # Test sea-staging
#   scripts/monitoring/smoke-test.sh --host=prod-server   # Test any SSH host
set -o pipefail

# --- Parse arguments ---
SSH_HOST="sea-staging"
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
  esac
done

REMOTE_DIR="/opt/pharkie-ojs-plugins"
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.staging.yml"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

# Read env values from remote .env
WP_HOME=$($SSH_CMD "grep '^WP_HOME=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_BASE_URL=$($SSH_CMD "grep '^OJS_BASE_URL=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_PATH=$($SSH_CMD "grep '^OJS_JOURNAL_PATH=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"

# Basic auth credentials for WP staging (Caddy)
WP_AUTH_USER=$($SSH_CMD "grep '^CADDY_WP_AUTH_USER=' $REMOTE_DIR/.env | cut -d= -f2")
WP_AUTH_PASS=$($SSH_CMD "grep '^CADDY_WP_AUTH_PASS=' $REMOTE_DIR/.env | cut -d= -f2")
if [ -n "$WP_AUTH_USER" ] && [ -n "$WP_AUTH_PASS" ]; then
  WP_CURL_AUTH="-u $WP_AUTH_USER:$WP_AUTH_PASS"
else
  WP_CURL_AUTH=""
fi

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

remote() {
  $SSH_CMD "cd $REMOTE_DIR && $*" 2>&1
}

wp_cli() {
  remote "$COMPOSE exec -T wp wp --allow-root $*"
}

echo "=== Smoke tests: $SSH_HOST ==="
echo "    WP:  $WP_HOME"
echo "    OJS: $OJS_BASE_URL"
echo ""

# --- 1. WP HTTP ---
echo "1. WordPress HTTP"
WP_STATUS=$(curl -s -o /dev/null -w '%{http_code}' $WP_CURL_AUTH "$WP_HOME" 2>/dev/null) || WP_STATUS="000"
if [ "$WP_STATUS" = "200" ] || [ "$WP_STATUS" = "301" ] || [ "$WP_STATUS" = "302" ]; then
  pass "WP responds (HTTP $WP_STATUS)"
else
  fail "WP not responding (HTTP $WP_STATUS)"
fi

# --- 1b. WP Admin page (catches .env permission issues that WP-CLI misses) ---
echo "1b. WordPress Admin"
WP_ADMIN_STATUS=$(curl -s -o /dev/null -w '%{http_code}' $WP_CURL_AUTH "$WP_HOME/wp/wp-admin/" 2>/dev/null) || WP_ADMIN_STATUS="000"
WP_ADMIN_BODY=$(curl -s $WP_CURL_AUTH "$WP_HOME/wp/wp-admin/" 2>/dev/null | head -20)
if echo "$WP_ADMIN_BODY" | grep -qi "Fatal error\|Exception\|unable to read"; then
  fail "WP Admin has PHP fatal error"
elif [ "$WP_ADMIN_STATUS" = "200" ] || [ "$WP_ADMIN_STATUS" = "301" ] || [ "$WP_ADMIN_STATUS" = "302" ]; then
  pass "WP Admin responds (HTTP $WP_ADMIN_STATUS)"
else
  fail "WP Admin not responding (HTTP $WP_ADMIN_STATUS)"
fi

# --- 1c. OJS Admin page ---
echo "1c. OJS Admin"
OJS_ADMIN_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$OJS_JOURNAL_URL/management/settings/access" 2>/dev/null) || OJS_ADMIN_STATUS="000"
OJS_ADMIN_BODY=$(curl -s "$OJS_JOURNAL_URL/management/settings/access" 2>/dev/null | head -30)
if echo "$OJS_ADMIN_BODY" | grep -qi "Fatal error\|Exception\|404 Not Found"; then
  fail "OJS Admin page error (HTTP $OJS_ADMIN_STATUS)"
elif [ "$OJS_ADMIN_STATUS" = "200" ] || [ "$OJS_ADMIN_STATUS" = "302" ]; then
  pass "OJS Admin responds (HTTP $OJS_ADMIN_STATUS)"
else
  fail "OJS Admin not responding (HTTP $OJS_ADMIN_STATUS)"
fi

# --- 2. OJS HTTP ---
echo "2. OJS HTTP"
OJS_STATUS=$(curl -s -o /dev/null -w '%{http_code}' "$OJS_BASE_URL" 2>/dev/null) || OJS_STATUS="000"
if [ "$OJS_STATUS" = "200" ] || [ "$OJS_STATUS" = "301" ] || [ "$OJS_STATUS" = "302" ]; then
  pass "OJS responds (HTTP $OJS_STATUS)"
else
  fail "OJS not responding (HTTP $OJS_STATUS)"
fi

# --- 2b. Adminer ---
echo "2b. Adminer"
ADMINER_STATUS=$(remote "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8082") || ADMINER_STATUS="000"
if [ "$ADMINER_STATUS" = "200" ]; then
  pass "Adminer responds (HTTP $ADMINER_STATUS)"
else
  fail "Adminer not responding (HTTP $ADMINER_STATUS)" "Expected on 127.0.0.1:8082"
fi

# --- 3. WP REST API ---
echo "3. WordPress REST API"
WP_API_STATUS=$(curl -s -o /dev/null -w '%{http_code}' $WP_CURL_AUTH "$WP_HOME/wp-json/" 2>/dev/null) || WP_API_STATUS="000"
if [ "$WP_API_STATUS" = "200" ]; then
  pass "WP REST API responds"
else
  fail "WP REST API not responding (HTTP $WP_API_STATUS)"
fi

# --- 4. OJS ping ---
echo "4. OJS plugin ping"
PING=$(remote "$COMPOSE exec -T ojs curl -sf http://localhost:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/ping") || PING=""
if echo "$PING" | grep -q '"status":"ok"'; then
  pass "OJS plugin responds to ping"
else
  fail "OJS plugin ping failed" "$PING"
fi

# --- 5. OJS preflight (auth + IP + compatibility) ---
echo "5. OJS preflight"
API_KEY=$($SSH_CMD "grep '^WPOJS_API_KEY_SECRET=' $REMOTE_DIR/.env | cut -d= -f2")
PREFLIGHT=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/preflight") || PREFLIGHT=""
if echo "$PREFLIGHT" | grep -q '"compatible":true'; then
  CHECKS=$(echo "$PREFLIGHT" | grep -o '"ok":true' | wc -l)
  pass "Preflight passes ($CHECKS checks OK)"
else
  fail "Preflight failed" "$PREFLIGHT"
fi

# --- 6. WP test-connection ---
echo "6. WP-CLI test-connection"
TC_OUTPUT=$(wp_cli "ojs-sync test-connection") || TC_OUTPUT=""
if echo "$TC_OUTPUT" | grep -q "Connection test passed"; then
  pass "test-connection passes"
else
  fail "test-connection failed" "$(echo "$TC_OUTPUT" | tail -3)"
fi

# --- 7. Required plugins active ---
echo "7. Required plugins"
PLUGINS=$(wp_cli "plugin list --status=active --format=csv --fields=name") || PLUGINS=""
ALL_ACTIVE=true
for PLUGIN in woocommerce woocommerce-subscriptions woocommerce-memberships ultimate-member wpojs-sync; do
  if echo "$PLUGINS" | grep -q "^$PLUGIN$"; then
    pass "$PLUGIN active"
  else
    fail "$PLUGIN not active"
    ALL_ACTIVE=false
  fi
done

# --- 8. OJS subscription types ---
echo "8. OJS subscription types"
SUB_TYPES=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/subscription-types") || SUB_TYPES=""
TYPE_COUNT=$(echo "$SUB_TYPES" | grep -o '"id"' | wc -l)
if [ "$TYPE_COUNT" -gt "0" ]; then
  pass "$TYPE_COUNT subscription type(s) configured"
else
  fail "No subscription types found" "$SUB_TYPES"
fi

# --- 8b. Product-to-type mapping validation ---
echo "8b. Product-to-type mapping"
MAPPING_JSON=$(wp_cli "eval '
  \$mapping = get_option(\"wpojs_type_mapping\", []);
  echo json_encode(\$mapping);
'") || MAPPING_JSON="{}"
MAPPING_OK=true
if [ "$MAPPING_JSON" = "{}" ] || [ "$MAPPING_JSON" = "[]" ] || [ -z "$MAPPING_JSON" ]; then
  fail "No product-to-type mappings configured"
  MAPPING_OK=false
else
  # Check each mapped product exists in WP and each OJS type exists
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
  # Check OJS type IDs exist in subscription types response
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

# --- 9. Single user sync round-trip ---
echo "9. Sync round-trip"
TEST_EMAIL="smoke-test-$(date +%s)@test.invalid"
TEST_LOGIN="smoke_$(date +%s)"
SYNC_OK=true

# Create temp WP user + active WCS subscription
WP_USER_ID=$(wp_cli "user create $TEST_LOGIN $TEST_EMAIL --role=subscriber --first_name=Smoke --last_name=Test --porcelain") || WP_USER_ID=""
if [ -z "$WP_USER_ID" ] || ! [[ "$WP_USER_ID" =~ ^[0-9]+$ ]]; then
  fail "Could not create test user" "$WP_USER_ID"
  SYNC_OK=false
fi

if [ "$SYNC_OK" = true ]; then
  # Create active WCS subscription (required for sync to recognise user as a member)
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
  # Sync the user
  SYNC_OUTPUT=$(wp_cli "ojs-sync sync --member=$TEST_EMAIL --yes" 2>&1) || true
  # Run Action Scheduler to process the queued job
  wp_cli "action-scheduler run" > /dev/null 2>&1 || true

  # Verify OJS user exists
  OJS_USER=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' 'http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/users?email=$TEST_EMAIL'") || OJS_USER=""
  if echo "$OJS_USER" | grep -q "$TEST_EMAIL"; then
    pass "User synced to OJS"

    # Verify subscription exists
    OJS_USER_ID=$(echo "$OJS_USER" | grep -o '"userId":[0-9]*' | head -1 | cut -d: -f2)
    if [ -n "$OJS_USER_ID" ]; then
      OJS_SUBS=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' 'http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/subscriptions?userId=$OJS_USER_ID'") || OJS_SUBS=""
      if echo "$OJS_SUBS" | grep -q '"status"'; then
        pass "OJS subscription created"

        # --- I3: Password login verification ---
        # Set a known password, sync it to OJS, then verify login works
        wp_cli "user update $WP_USER_ID --user_pass=SmokeTest123!" > /dev/null 2>&1 || true
        wp_cli "action-scheduler run" > /dev/null 2>&1 || true
        LOGIN_RESPONSE=$(remote "$COMPOSE exec -T wp curl -s -L -c /tmp/smoke-cookies -X POST \
          'http://ojs:80/index.php/$OJS_JOURNAL_PATH/login/signIn' \
          -d 'username=${TEST_LOGIN}&password=SmokeTest123!'") || LOGIN_RESPONSE=""
        if echo "$LOGIN_RESPONSE" | grep -q 'pkp_navigation_user\|logOut'; then
          pass "OJS password login works"
        else
          fail "OJS password login failed" "Login page did not show user navigation"
        fi

        # --- I8: Status change verification ---
        wp_cli "eval '\$sub = wcs_get_subscription($SUB_ID); \$sub->update_status(\"expired\");'" > /dev/null 2>&1 || true
        # Run Action Scheduler multiple times — previous steps may have queued
        # other jobs (password sync) that need to drain before expire runs.
        EXPIRE_OK=false
        for _run in 1 2 3; do
          wp_cli "action-scheduler run" > /dev/null 2>&1 || true
          OJS_SUB_STATUS=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' 'http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/subscriptions?userId=$OJS_USER_ID'") || OJS_SUB_STATUS=""
          if echo "$OJS_SUB_STATUS" | grep -q '"status":16'; then
            EXPIRE_OK=true
            break
          fi
          sleep 1
        done
        if [ "$EXPIRE_OK" = true ]; then
          pass "OJS subscription expired after status change"
        else
          fail "OJS subscription status not updated to expired" "$OJS_SUB_STATUS"
        fi
      else
        fail "OJS subscription not found" "$OJS_SUBS"
      fi
    fi
  else
    fail "User not found in OJS after sync" "$OJS_USER"
  fi

  # --- I8: GDPR-verified cleanup ---
  wp_cli "user delete $WP_USER_ID --yes" > /dev/null 2>&1 || true
  # Action Scheduler may have other jobs queued ahead — run multiple times
  for _run in 1 2 3; do
    wp_cli "action-scheduler run" > /dev/null 2>&1 || true
  done
  # Verify OJS user is anonymised: original email should no longer resolve
  # (the delete handler changes email to deleted_<id>@anonymised.invalid)
  CLEANUP_OK=false
  for _i in 1 2 3; do
    OJS_DELETED_USER=$(remote "$COMPOSE exec -T wp curl -sf -H 'Authorization: Bearer $API_KEY' 'http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs/users?email=$TEST_EMAIL'") || OJS_DELETED_USER=""
    if echo "$OJS_DELETED_USER" | grep -q '"found":false'; then
      CLEANUP_OK=true
      break
    fi
    sleep 2
    wp_cli "action-scheduler run" > /dev/null 2>&1 || true
  done
  if [ "$CLEANUP_OK" = true ]; then
    pass "OJS user anonymised after WP deletion"
    # Hard-delete the anonymised shell — smoke test users shouldn't persist
    if [ -n "$OJS_USER_ID" ]; then
      remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -e \"
        DELETE FROM subscriptions WHERE user_id = $OJS_USER_ID;
        DELETE FROM event_log WHERE user_id = $OJS_USER_ID;
        DELETE FROM user_settings WHERE user_id = $OJS_USER_ID;
        DELETE FROM user_user_groups WHERE user_id = $OJS_USER_ID;
        DELETE FROM users WHERE user_id = $OJS_USER_ID;
      \"'" > /dev/null 2>&1 && pass "Smoke test user hard-deleted from OJS" || echo "  [WARN] Could not hard-delete smoke test user from OJS"
    fi
  else
    fail "OJS user not properly cleaned up after WP deletion" "$OJS_DELETED_USER"
  fi
fi

# --- 10. OJS search ---
echo "10. OJS search"
# Coverage check: distinct submissions indexed vs published. Catches the bug
# where rebuildSearchIndex.php runs but the queued jobs are wiped before they
# process — only freshly-published articles land in the index via the normal
# publish path, leaving the archive unsearchable.
INDEXED=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(DISTINCT submission_id) FROM submission_search_objects\"'") || INDEXED="0"
INDEXED=$(echo "$INDEXED" | tr -d '[:space:]')
PUBLISHED=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM submissions WHERE status = 3 AND current_publication_id IS NOT NULL\"'") || PUBLISHED="0"
PUBLISHED=$(echo "$PUBLISHED" | tr -d '[:space:]')

if [ "$PUBLISHED" -gt "0" ] 2>/dev/null; then
  MISSING=$((PUBLISHED - INDEXED))
  PCT=$((INDEXED * 100 / PUBLISHED))
  if [ "$MISSING" -gt 20 ] || [ "$PCT" -lt 90 ]; then
    fail "Search index coverage: $INDEXED/$PUBLISHED submissions indexed (${PCT}%, $MISSING missing)" \
         "Run: scripts/ojs/blast-queue.sh --host=$SSH_HOST after rebuildSearchIndex.php"
  else
    pass "Search index: $INDEXED/$PUBLISHED submissions indexed (${PCT}%)"
  fi
else
  fail "Could not count published submissions"
fi

# HTTP search test — oldest and newest author bands. If only the newest succeeds
# and the oldest fails, the archive is unindexed (2026-04 bug signature).
# CHAR(102,97,109,105,108,121,78,97,109,101) = 'familyName'
test_search_author() {
  local label="$1" sql="$2"
  local author
  author=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"$sql\"'") || author=""
  author=$(echo "$author" | tr -d '[:space:]')
  if [ -z "$author" ]; then
    echo "  [SKIP] No $label author found for HTTP search test"
    return
  fi
  local body
  body=$(curl -s "${OJS_JOURNAL_URL}/search/search?authors=${author}" 2>/dev/null)
  if echo "$body" | grep -qi "obj_article_summary\|search_results\|$author"; then
    pass "Search for $label author '$author' returns results"
  elif echo "$body" | grep -qi "No items found\|no results"; then
    fail "Search for $label author '$author' returned no results (index may need rebuilding)"
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

# --- 11. Reconciliation ---
echo "11. Reconciliation"
RECON_OUTPUT=$(wp_cli "ojs-sync reconcile") || RECON_OUTPUT=""
if echo "$RECON_OUTPUT" | grep -q "Reconciliation complete"; then
  pass "Reconciliation completes successfully"
else
  fail "Reconciliation failed" "$(echo "$RECON_OUTPUT" | tail -3)"
fi

# --- 12. Backup health ---
echo "12. Backup health"
BACKUP_CRON=$($SSH_CMD "sudo crontab -l 2>/dev/null | grep -F 'backup-ojs-db.sh' || true")
if [ -n "$BACKUP_CRON" ]; then
  pass "Backup cron installed"
else
  fail "Backup cron not installed (run: scripts/infra/pull-ojs-backup.sh --install-cron)"
fi

BACKUP_KEY=$($SSH_CMD "test -f /opt/backups/ojs/.backup-key && echo 'exists' || echo 'missing'")
if [ "$BACKUP_KEY" = "exists" ]; then
  pass "Backup encryption key present"
else
  fail "Backup encryption key missing at /opt/backups/ojs/.backup-key"
fi

LATEST_BACKUP=$($SSH_CMD "ls -t /opt/backups/ojs/daily/ojs-*.sql.gz.enc 2>/dev/null | head -1")
if [ -n "$LATEST_BACKUP" ]; then
  BACKUP_AGE=$($SSH_CMD "echo \$(( (\$(date +%s) - \$(stat -c %Y '$LATEST_BACKUP')) / 3600 ))h")
  BACKUP_SIZE=$($SSH_CMD "stat -c%s '$LATEST_BACKUP' | numfmt --to=iec")
  pass "Latest backup: $(basename "$LATEST_BACKUP") ($BACKUP_SIZE, ${BACKUP_AGE} old)"
else
  fail "No encrypted backups found in /opt/backups/ojs/daily/"
fi

# --- Summary ---
echo ""
echo "=== Results: $PASSED/$TOTAL passed, $FAILED failed ==="
if [ "$FAILED" -gt "0" ]; then
  exit 1
fi
