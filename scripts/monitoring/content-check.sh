#!/bin/bash
# Post-deployment content checks for the journal site.
# Verifies key pages serve correct content (no login required).
# Runs FROM the devcontainer (or any machine with network access).
#
# Usage:
#   scripts/monitoring/content-check.sh --host=sea-live
#   scripts/monitoring/content-check.sh --host=sea-staging
set -o pipefail

# --- Parse arguments ---
SSH_HOST="sea-staging"
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
  esac
done

REMOTE_DIR="/opt/pharkie-ojs-plugins"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

OJS_BASE_URL=$($SSH_CMD "grep '^OJS_BASE_URL=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_PATH=$($SSH_CMD "grep '^OJS_JOURNAL_PATH=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"

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

TMPBODY=$(mktemp)
trap "rm -f $TMPBODY" EXIT

fetch() {
  curl -sL "$1" > "$TMPBODY" 2>/dev/null
}

check_contains() {
  local needle="$1"
  local desc="$2"
  if grep -qi "$needle" "$TMPBODY"; then
    pass "$desc"
  else
    fail "$desc" "Expected to find: $needle"
  fi
}

echo "=== Content checks: $SSH_HOST ==="
echo "    OJS: $OJS_JOURNAL_URL"
echo ""

# --- 1. Journal home ---
echo "1. Journal home"
fetch "$OJS_JOURNAL_URL"
check_contains "Existential Analysis" "Contains journal title"

# --- 2. Current issue ---
echo "2. Current issue"
fetch "$OJS_JOURNAL_URL/issue/current"
check_contains "article/view" "Contains article links"
check_contains "Vol." "Contains volume number"

# --- 3. Known article with DOIs (vol 37.1, article 9795) ---
echo "3. Article page (9795 — vol 37.1)"
fetch "$OJS_JOURNAL_URL/article/view/9795"
check_contains "citation_title" "Contains citation meta tag"
check_contains "citation_author" "Contains author meta tag"
check_contains "doi.org/10.65828" "Contains article DOI link"
check_contains "Full Text" "Contains Full Text section"
check_contains "doi.org/10" "Contains reference DOI links"

# --- 4. Early volume article (vol 1, article 1426) ---
echo "4. Early volume article (1426 — vol 1)"
fetch "$OJS_JOURNAL_URL/article/view/1426"
check_contains "Existential Analysis" "Contains citation meta"
check_contains "Heaton" "Contains author name"
check_contains "Full Text" "Contains Full Text section"

# --- 5. Archive page ---
echo "5. Archive page"
fetch "$OJS_JOURNAL_URL/issue/archive"
check_contains "Vol. 37" "Contains latest volume (Vol. 37)"
ISSUE_COUNT=$(grep -c 'issue/view' "$TMPBODY")
if [ "$ISSUE_COUNT" -ge 25 ]; then
  pass "Contains $ISSUE_COUNT issue links (expected 25+)"
else
  fail "Only $ISSUE_COUNT issue links (expected 25+)"
fi
check_contains "2014" "Spans back to at least 2014"

# --- 6. PDF galley loads (not blocked by X-Frame-Options) ---
echo "6. PDF galley page (8788/33950)"
HTTP_CODE=$(curl -sL -o "$TMPBODY" -w '%{http_code}' "$OJS_JOURNAL_URL/article/view/8788/33950" 2>/dev/null)
if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 400 ]; then
  pass "PDF galley returns HTTP $HTTP_CODE"
else
  fail "PDF galley returns HTTP $HTTP_CODE" "Expected 2xx/3xx"
fi
check_contains "pdfJsViewer\|pdfCanvasContainer" "Contains PDF viewer embed"
# Check that X-Frame-Options is not DENY (would block the PDF.js iframe)
XFO=$(curl -sI "$OJS_JOURNAL_URL/article/view/8788/33950" 2>/dev/null | grep -i 'x-frame-options' | tr -d '\r')
if echo "$XFO" | grep -qi "DENY"; then
  fail "X-Frame-Options is DENY (blocks PDF.js iframe)" "$XFO"
else
  pass "X-Frame-Options allows same-origin iframes"
fi

# --- 7. Article-page sweep ---
# 10 random published submissions with a 5s per-request timeout. Catches
# cascading slow-query regressions on the article view path (e.g. the 2026-04
# recommendBySimilarity failure where every article page hung after the search
# index grew). One single-URL probe misses these — the failure only surfaces
# once several pages stack up against a shared DB bottleneck.
echo "7. Article-page sweep (10 random published submissions, 5s timeout each)"
IDS=$($SSH_CMD "cd $REMOTE_DIR && docker compose exec -T ojs-db bash -c 'mariadb -u\$MYSQL_USER -p\$MYSQL_PASSWORD \$MYSQL_DATABASE -N -e \"
  SELECT s.submission_id FROM submissions s WHERE s.status = 3 AND s.current_publication_id IS NOT NULL ORDER BY RAND() LIMIT 10
\"'" 2>/dev/null | tr -d '\r' | tr '\n' ' ')
SLOW=0
MISSING=0
for id in $IDS; do
  [ -z "$id" ] && continue
  TIME_HTTP=$(curl -sL -o /dev/null --max-time 5 -w '%{time_total} %{http_code}' "$OJS_JOURNAL_URL/article/view/$id" 2>/dev/null)
  code=${TIME_HTTP##* }
  time=${TIME_HTTP%% *}
  if [ "$code" = "000" ]; then
    SLOW=$((SLOW + 1))
    echo "  [SLOW] article/view/$id timed out (>5s)"
  elif [ "$code" != "200" ]; then
    MISSING=$((MISSING + 1))
    echo "  [MISS] article/view/$id returned HTTP $code"
  fi
done
TOTAL_SWEEP=$(echo $IDS | wc -w)
if [ "$SLOW" -gt 0 ]; then
  fail "$SLOW/$TOTAL_SWEEP article pages timed out (>5s)" "Signals a cascading slow-query regression"
elif [ "$MISSING" -gt 0 ]; then
  fail "$MISSING/$TOTAL_SWEEP article pages returned non-200"
else
  pass "All $TOTAL_SWEEP sampled article pages respond in <5s"
fi

echo ""
echo "=== Results: $PASSED/$TOTAL passed, $FAILED failed ==="
[ "$FAILED" -gt 0 ] && exit 1
exit 0
