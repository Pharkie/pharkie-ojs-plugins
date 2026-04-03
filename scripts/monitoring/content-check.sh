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

echo ""
echo "=== Results: $PASSED/$TOTAL passed, $FAILED failed ==="
[ "$FAILED" -gt 0 ] && exit 1
exit 0
