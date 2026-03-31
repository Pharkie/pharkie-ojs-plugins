#!/bin/bash
# Load test for staging/production VPS.
# Runs FROM the devcontainer using 'hey' against the remote environment.
# Monitors server resources (CPU, memory, Docker) during the test.
#
# Usage:
#   scripts/monitoring/load-test.sh                      # Test sea-staging
#   scripts/monitoring/load-test.sh --host=prod-server   # Test any SSH host
#   scripts/monitoring/load-test.sh --light              # Lighter load (10 concurrent)
#
# Requires: hey (https://github.com/rakyll/hey)
set -o pipefail

# --- Parse arguments ---
SSH_HOST="sea-staging"
CONCURRENCY=50
REQUESTS=500
API_CONCURRENCY=20
API_REQUESTS=200
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
    --light) CONCURRENCY=10; REQUESTS=100; API_CONCURRENCY=5; API_REQUESTS=50 ;;
  esac
done

REMOTE_DIR="/opt/pharkie-ojs-plugins"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

# Check hey is installed
if ! command -v hey &>/dev/null; then
  echo "ERROR: 'hey' not installed. Install: curl -sfL https://hey-release.s3.us-east-2.amazonaws.com/hey_linux_amd64 -o /usr/local/bin/hey && chmod +x /usr/local/bin/hey"
  exit 1
fi

# Read env values
WP_HOME=$($SSH_CMD "grep '^WP_HOME=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_BASE_URL=$($SSH_CMD "grep '^OJS_BASE_URL=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_PATH=$($SSH_CMD "grep '^OJS_JOURNAL_PATH=' $REMOTE_DIR/.env | cut -d= -f2")
API_KEY=$($SSH_CMD "grep '^WPOJS_API_KEY_SECRET=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_URL="$OJS_BASE_URL/index.php/$OJS_JOURNAL_PATH"

# Find an article URL for testing
ARTICLE_PATH=$(curl -sf "$OJS_JOURNAL_URL/issue/view/1" 2>/dev/null \
  | grep -oP "/$OJS_JOURNAL_PATH/article/view/\\d+" | head -1) || ARTICLE_PATH=""
ARTICLE_URL="$OJS_BASE_URL/index.php${ARTICLE_PATH:-/$OJS_JOURNAL_PATH}"

echo "============================================"
echo "  Load Test: $SSH_HOST"
echo "============================================"
echo "  WP:          $WP_HOME"
echo "  OJS:         $OJS_BASE_URL"
echo "  Article:     $ARTICLE_URL"
echo "  Concurrency: $CONCURRENCY (pages), $API_CONCURRENCY (API)"
echo "  Requests:    $REQUESTS (pages), $API_REQUESTS (API)"
echo ""

# --- Server baseline ---
echo "=== Server baseline ==="
$SSH_CMD "
  echo 'CPU cores:' \$(nproc)
  echo 'Memory:' \$(free -h | awk '/^Mem:/{print \$2 \" total, \" \$3 \" used, \" \$7 \" available\"}')
  echo 'Load avg:' \$(cat /proc/loadavg | cut -d' ' -f1-3)
  echo 'Docker containers:' \$(docker ps -q | wc -l) running
  echo ''
  echo 'Container memory usage:'
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' 2>/dev/null
"
echo ""

# --- Start server monitoring in background ---
MONITOR_LOG=$(mktemp)
$SSH_CMD "
  while true; do
    echo \"--- \$(date +%H:%M:%S) ---\"
    echo \"Load: \$(cat /proc/loadavg | cut -d' ' -f1-3)\"
    free -h | awk '/^Mem:/{print \"Memory: \" \$3 \" used / \" \$2 \" (\" \$7 \" available)\"}'
    docker stats --no-stream --format '{{.Name}}: CPU {{.CPUPerc}} | Mem {{.MemUsage}}' 2>/dev/null
    echo ''
    sleep 5
  done
" > "$MONITOR_LOG" 2>&1 &
MONITOR_PID=$!

cleanup() {
  kill $MONITOR_PID 2>/dev/null
  wait $MONITOR_PID 2>/dev/null
  rm -f "$MONITOR_LOG"
}
trap cleanup EXIT

# --- Helper to run and summarise a hey test ---
PASS=0
FAIL=0

run_test() {
  local NAME="$1"
  local URL="$2"
  local C="$3"
  local N="$4"
  shift 4
  local EXTRA_ARGS="$*"

  echo "--- $NAME ---"
  echo "    $URL  (${C}c × ${N}r)"

  local OUTPUT
  OUTPUT=$(hey -c "$C" -n "$N" -t 30 $EXTRA_ARGS "$URL" 2>&1)

  # Extract key metrics
  local P50 P95 P99 RPS ERRORS
  P50=$(echo "$OUTPUT" | grep '50%' | awk '{print $4}')
  P95=$(echo "$OUTPUT" | grep '95%' | awk '{print $4}')
  P99=$(echo "$OUTPUT" | grep '99%' | awk '{print $4}')
  RPS=$(echo "$OUTPUT" | grep 'Requests/sec' | awk '{print $2}')
  ERRORS=$(echo "$OUTPUT" | grep -c '\[5[0-9][0-9]\]' || true)
  STATUS_DIST=$(echo "$OUTPUT" | grep '^\s*\[' | head -5)

  echo "    p50: ${P50}s | p95: ${P95}s | p99: ${P99}s | rps: $RPS"
  if [ -n "$STATUS_DIST" ]; then
    echo "    Status codes: $(echo "$STATUS_DIST" | tr '\n' ' ')"
  fi

  # Check pass criteria
  local P95_MS
  P95_MS=$(echo "$P95" | awk '{printf "%d", $1 * 1000}' 2>/dev/null) || P95_MS=99999

  local THRESHOLD="$5"
  [ -z "$THRESHOLD" ] && THRESHOLD=2000

  if [ "$P95_MS" -le "$THRESHOLD" ] && [ "$ERRORS" -eq 0 ]; then
    echo "    [PASS] p95 ${P95}s <= ${THRESHOLD}ms, 0 errors"
    PASS=$((PASS + 1))
  else
    echo "    [FAIL] p95 ${P95}s (limit ${THRESHOLD}ms), $ERRORS server errors"
    FAIL=$((FAIL + 1))
  fi
  echo ""
}

echo "=== Running load tests ==="
echo ""

# 1. OJS journal homepage
run_test "OJS journal homepage" "$OJS_JOURNAL_URL" $CONCURRENCY $REQUESTS

# 2. OJS article page
run_test "OJS article page" "$ARTICLE_URL" $CONCURRENCY $REQUESTS

# 3. OJS API preflight
run_test "OJS API preflight" "$OJS_JOURNAL_URL/api/v1/wpojs/ping" $API_CONCURRENCY $API_REQUESTS

# 4. WP homepage
run_test "WP homepage" "$WP_HOME" $CONCURRENCY $REQUESTS

# 5. WP REST API
run_test "WP REST API" "$WP_HOME/wp-json/" $API_CONCURRENCY $API_REQUESTS

# --- Server state after load ---
echo "=== Server state after load ==="
$SSH_CMD "
  echo 'Load avg:' \$(cat /proc/loadavg | cut -d' ' -f1-3)
  free -h | awk '/^Mem:/{print \"Memory: \" \$3 \" used / \" \$2 \" (\" \$7 \" available)\"}'
  echo ''
  echo 'Container stats:'
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' 2>/dev/null
  echo ''
  # Check for OOM kills
  OOMS=\$(dmesg 2>/dev/null | grep -ci 'oom\|out of memory' || true)
  if [ \"\$OOMS\" -gt 0 ]; then
    echo 'WARNING: OOM kills detected!'
    dmesg | grep -i 'oom\|out of memory' | tail -5
  else
    echo 'No OOM kills detected.'
  fi
"
echo ""

# --- Peak load during test ---
echo "=== Peak server load during test ==="
if [ -f "$MONITOR_LOG" ]; then
  # Find peak load average
  PEAK_LOAD=$(grep '^Load:' "$MONITOR_LOG" | awk '{print $2}' | sort -rn | head -1)
  echo "  Peak 1-min load avg: $PEAK_LOAD ($(nproc 2>/dev/null || echo '?') cores on VPS)"

  # Find peak memory usage
  PEAK_MEM=$(grep '^Memory:' "$MONITOR_LOG" | awk '{print $2}' | sort -rh | head -1)
  echo "  Peak memory used: $PEAK_MEM"
fi
echo ""

# --- Summary ---
TOTAL=$((PASS + FAIL))
echo "============================================"
echo "  Results: $PASS/$TOTAL passed, $FAIL failed"
echo "============================================"

if [ "$FAIL" -gt 0 ]; then
  echo ""
  echo "  Consider upgrading the VPS if failures are due to"
  echo "  high latency under load rather than one-off spikes."
  exit 1
fi
