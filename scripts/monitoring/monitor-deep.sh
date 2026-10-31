#!/bin/bash
# Deep monitoring checks for the journal (OJS) — runs daily.
# Includes everything from monitor-safe.sh PLUS slower checks: the sync API's
# access control, the members' site's recent sync traffic, backup health,
# search index, DB size. Read-only: nothing here writes to OJS beyond the sync
# API's own request log, which records every call.
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
echo "PHASE 2: Deep (read-only, slower) checks"
echo "================================================================"

# ============================================================
# D1. SYNC API (READ-ONLY)
# ============================================================
echo ""
echo "--- Sync API ---"

# The live members' WordPress (on Krystal, not this box) calls the journal's
# wpojs API for every member who joins, renews or lapses. These checks prove,
# without writing anything, that the API is up, takes the key, reaches the
# journal's users, and refuses a caller without the key. They run from inside
# the ojs container against http://ojs:80, which the IP allowlist admits.
#
# The address below cannot exist (.invalid is reserved, RFC 2606), so the
# lookup must come back as a clean "not found". GET /users is read-only.
SYNC_API="http://ojs:80/index.php/$OJS_JOURNAL_PATH/api/v1/wpojs"
PROBE_EMAIL="monitor-absent@test.invalid"

# Prints the body, then the HTTP status on its own last line.
# Usage: sync_api_get <path> [api-key]
sync_api_get() {
  local path="$1" key="${2:-}" auth=""
  [ -n "$key" ] && auth="-H 'Authorization: Bearer $key'"
  remote "$COMPOSE exec -T ojs curl -s -w '\n%{http_code}' $auth '$SYNC_API/$path'"
}

# D1a. With the key: the lookup answers 200 {"found":false}
LOOKUP=$(sync_api_get "users?email=$PROBE_EMAIL" "$API_KEY") || LOOKUP=""
LOOKUP_STATUS=$(echo "$LOOKUP" | tail -1 | tr -d '[:space:]')
LOOKUP_BODY=$(echo "$LOOKUP" | sed '$d')
if [ "$LOOKUP_STATUS" = "200" ] && echo "$LOOKUP_BODY" | grep -q '"found":false'; then
  pass "Sync API accepts the key and looks up journal users (absent address: not found)"
else
  fail "Sync API lookup with the key failed (HTTP ${LOOKUP_STATUS:-none})" "${LOOKUP_BODY:-<empty — SSH or container may be down>}"
fi

# D1b. Without a key, and with a wrong one: 401. A 403 here would be the IP
# allowlist refusing the monitor, not the key check.
for KEY_CASE in "no key" "a wrong key"; do
  if [ "$KEY_CASE" = "no key" ]; then
    REFUSED=$(sync_api_get "users?email=$PROBE_EMAIL") || REFUSED=""
  else
    REFUSED=$(sync_api_get "users?email=$PROBE_EMAIL" "monitor-wrong-key") || REFUSED=""
  fi
  REFUSED_STATUS=$(echo "$REFUSED" | tail -1 | tr -d '[:space:]')
  if [ "$REFUSED_STATUS" = "401" ]; then
    pass "Sync API refuses a caller with $KEY_CASE (HTTP 401)"
  else
    fail "Sync API did not refuse a caller with $KEY_CASE (HTTP ${REFUSED_STATUS:-none}, expected 401)" "$(echo "$REFUSED" | sed '$d' | head -c 200)"
  fi
done

# D1c. The members' site's own traffic, from the API's request log. Server
# errors are the sync failing for real members; 1-2 in a day can be a blip
# the site retries, 3 or more is a fault. The last successful write is shown
# for the record only: a quiet day for joins and renewals is not an outage.
SYNC_5XX=$(require_remote "Sync API server errors (24h)" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM wpojs_api_log WHERE http_status >= 500 AND created_at > NOW() - INTERVAL 1 DAY\"'") || SYNC_5XX=""
if [ -n "$SYNC_5XX" ]; then
  if [ "$SYNC_5XX" -ge 3 ] 2>/dev/null; then
    fail "Sync API answered $SYNC_5XX requests with a server error in the last 24h (wpojs_api_log)"
  elif [ "$SYNC_5XX" -gt 0 ] 2>/dev/null; then
    warn "Sync API answered $SYNC_5XX request(s) with a server error in the last 24h (below the alert threshold of 3)"
    pass "Sync API server errors below alert threshold ($SYNC_5XX < 3)"
  else
    pass "No sync API server errors in the last 24h"
  fi
fi
# CHAR(71,69,84) = 'GET'
LAST_WRITE_H=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT IFNULL(TIMESTAMPDIFF(HOUR, MAX(created_at), NOW()), -1) FROM wpojs_api_log WHERE method <> CHAR(71,69,84) AND http_status < 300\"'" 2>/dev/null | tr -d '[:space:]')
if [ -n "$LAST_WRITE_H" ] && [ "$LAST_WRITE_H" -ge 0 ] 2>/dev/null; then
  info "Last successful sync write from the members' site: ${LAST_WRITE_H}h ago"
else
  info "No successful sync write in the request log (it keeps 30 days)"
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

# Slow-query probe: flags any ojs query running > 10s. Catches cascading
# article-page hangs where a DB-bound plugin (e.g. recommendBySimilarity)
# starts timing out after a corpus change. 2026-04-16 outage sat at 17
# concurrent 60-2000s queries on processlist before my check — one peek
# would have caught it instantly.
SLOW_QUERIES=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(*) FROM information_schema.processlist WHERE Command IN (\\\"Execute\\\",\\\"Query\\\") AND TIME > 10 AND User = \\\"ojs\\\"\"'" 2>/dev/null | tr -d '[:space:]')
if [ -n "$SLOW_QUERIES" ] && [ "$SLOW_QUERIES" -gt 0 ] 2>/dev/null; then
  fail "$SLOW_QUERIES OJS query/queries running >10s on ojs-db (see SHOW PROCESSLIST)"
elif [ -n "$SLOW_QUERIES" ]; then
  pass "No OJS queries running >10s"
fi

# Keyword-skew warning removed 2026-04-16: the warn query itself was slow
# (~2:46 on a 1400-article corpus — same GROUP BY over 4.2M rows that broke
# the e2e findSearchableTitleWord helper). The warning was originally added
# to flag corpus-wide tokens as a risk indicator for recommendBySimilarity,
# but that plugin is now replaced with smarterSimilarArticles which handles skew
# inherently via TF-IDF's max_df filter. The slow-query probe above + the
# article-page sweep in content-check.sh already catch the failure mode it
# was warning about, so the check was redundant at a heavy cost.
# See docs/ojs-issues-log.md #26.

# smarter_similar_articles cache (written by scripts/ojs/build_smarter_similar_articles.py).
# Two health checks: coverage (build actually wrote rows) and staleness
# (scheduled rebuild is running). Skipped silently if the table doesn't exist
# yet (i.e. smarterSimilarArticles plugin hasn't been installed on this target).
SIMILAR_EXISTS=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SHOW TABLES LIKE \\\"smarter_similar_articles\\\"\"'" 2>/dev/null | tr -d '[:space:]')
if [ -n "$SIMILAR_EXISTS" ]; then
  SIMILAR_CACHED=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT COUNT(DISTINCT submission_id) FROM smarter_similar_articles\"'" 2>/dev/null | tr -d '[:space:]')
  # Reuse $PUBLISHED from earlier search-index coverage check
  if [ -n "$SIMILAR_CACHED" ] && [ -n "$PUBLISHED" ] && [ "$PUBLISHED" -gt 0 ] 2>/dev/null; then
    SIMILAR_PCT=$((SIMILAR_CACHED * 100 / PUBLISHED))
    # Healthy state is ~93-95% (remaining ~5% legitimately have no match
    # above MIN_SCORE — editorials, tribute pieces, outliers). Tightened from
    # the initial 50/80 to catch real-world failures earlier: 93% warn catches
    # a single missed rebuild on a typical day; 85% fail catches genuine
    # rebuild failure before readers notice.
    if [ "$SIMILAR_PCT" -lt 85 ]; then
      fail "smarter_similar_articles cache low: $SIMILAR_CACHED/$PUBLISHED articles (${SIMILAR_PCT}%)" \
           "Run: python3 scripts/ojs/build_smarter_similar_articles.py --target=<host>"
    elif [ "$SIMILAR_PCT" -lt 93 ]; then
      warn "smarter_similar_articles cache partial: $SIMILAR_CACHED/$PUBLISHED (${SIMILAR_PCT}%)"
    else
      pass "smarter_similar_articles cache: $SIMILAR_CACHED/$PUBLISHED articles (${SIMILAR_PCT}%)"
    fi
  fi
  # Staleness: oldest computed_at across all rows. The rebuild is weekly
  # (Mondays 04:15 UTC, sea-ojs-private), so this 06:30 check reads ~2h on a
  # Monday and ~146h on a Sunday. Limits are 168h plus a day's grace: one
  # missed Monday run warns, and it fails the day after.
  SIMILAR_AGE_H=$(remote "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT IFNULL(TIMESTAMPDIFF(HOUR, MIN(computed_at), NOW()), -1) FROM smarter_similar_articles\"'" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$SIMILAR_AGE_H" ] && [ "$SIMILAR_AGE_H" -ge 0 ] 2>/dev/null; then
    if [ "$SIMILAR_AGE_H" -gt 174 ]; then   # weekly (168h) + a day's grace
      fail "smarter_similar_articles cache is ${SIMILAR_AGE_H}h old (>174h — weekly rebuild failing)"
    elif [ "$SIMILAR_AGE_H" -gt 150 ]; then # past Sunday's ~146h: Monday's run missed
      warn "smarter_similar_articles cache is ${SIMILAR_AGE_H}h old (>150h — one missed weekly rebuild)"
    else
      pass "smarter_similar_articles cache age: ${SIMILAR_AGE_H}h"
    fi
  fi
fi

# ============================================================
# D5. DATABASE SIZES
# ============================================================
echo ""
echo "--- Database ---"

DB_SIZE=$(require_remote "ojs-db database size" "$COMPOSE exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"SELECT ROUND(SUM(data_length + index_length) / 1024 / 1024, 1) FROM information_schema.tables WHERE table_schema = DATABASE()\"'") || DB_SIZE=""
if [ -n "$DB_SIZE" ]; then
  DB_SIZE_INT=$(echo "$DB_SIZE" | cut -d. -f1)
  if [ "${DB_SIZE_INT:-0}" -gt 2048 ] 2>/dev/null; then
    fail "ojs-db database size ${DB_SIZE}MB exceeds 2GB"
  else
    pass "ojs-db database size: ${DB_SIZE}MB"
  fi
fi

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

# ============================================================
# D7. PENDING REBOOT / KERNEL PATCH STATE
# ============================================================
echo ""
echo "--- System Patch State ---"

# /var/run is tmpfs (wiped on boot), so reboot-required's mtime = how long a
# reboot has been pending since last boot. Nothing surfaced this before — a
# kernel ran 11 versions behind for 8 weeks unnoticed (2026-05-26). Lives in the
# DAILY check (not hourly) so a persistent "reboot pending" doesn't pin the
# hourly heartbeat red and mask acute issues. WARN immediately; escalate to FAIL
# (alert) after REBOOT_FAIL_DAYS, so a routine patch you'll reboot tomorrow isn't
# a page but a forgotten one is. Tune REBOOT_FAIL_DAYS to taste.
REBOOT_FAIL_DAYS=7
REBOOT_AGE_DAYS=$(remote "if [ -f /var/run/reboot-required ]; then echo \$(( ( \$(date +%s) - \$(stat -c %Y /var/run/reboot-required) ) / 86400 )); else echo NONE; fi") || REBOOT_AGE_DAYS=""
REBOOT_AGE_DAYS=$(echo "$REBOOT_AGE_DAYS" | tr -d '[:space:]')
if [ "$REBOOT_AGE_DAYS" = "NONE" ]; then
  pass "No pending reboot (kernel/library patches active)"
elif [[ "$REBOOT_AGE_DAYS" =~ ^[0-9]+$ ]]; then
  RUNNING_KERNEL=$(remote "uname -r" | tr -d '[:space:]')
  REBOOT_PKGS=$(remote "sort -u /var/run/reboot-required.pkgs 2>/dev/null | tr '\n' ' '")
  if [ "$REBOOT_AGE_DAYS" -ge "$REBOOT_FAIL_DAYS" ] 2>/dev/null; then
    fail "Reboot pending ${REBOOT_AGE_DAYS}d (>=${REBOOT_FAIL_DAYS}d) - running kernel ${RUNNING_KERNEL:-?}; schedule a maintenance-window reboot" "pkgs: ${REBOOT_PKGS:-?}"
  else
    warn "Reboot pending ${REBOOT_AGE_DAYS}d - running kernel ${RUNNING_KERNEL:-?} (escalates to FAIL at ${REBOOT_FAIL_DAYS}d)"
  fi
else
  warn "Could not read reboot-required state (got: ${REBOOT_AGE_DAYS:-<empty>})"
fi

# --- Deep Summary ---
echo ""
echo "=== Deep results: $PASSED/$TOTAL passed, $FAILED failed ==="

ping_heartbeat "$BETTERSTACK_HB_DAILY" "$((FAILED + (SAFE_EXIT > 0 ? 1 : 0)))" "$PASSED" "$TOTAL"

if [ "$SAFE_EXIT" -ne 0 ] || [ "$FAILED" -gt 0 ]; then
  exit 1
fi
