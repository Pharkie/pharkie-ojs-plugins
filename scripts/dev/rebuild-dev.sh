#!/bin/bash
# Full nuke-and-pave for the dev environment (devcontainer only).
# Tears down everything, rebuilds images, brings up the stack,
# runs setup, and optionally runs the e2e test suite.
#
# Usage:
#   scripts/dev/rebuild-dev.sh                          # Rebuild + tests
#   scripts/dev/rebuild-dev.sh --with-sample-data       # Rebuild + sample data + tests
#   scripts/dev/rebuild-dev.sh --skip-tests             # Rebuild without tests
#   scripts/dev/rebuild-dev.sh --with-sample-data --skip-tests
#
# Output is always tee'd to logs/rebuild-<timestamp>.log so it's recoverable.
#
# Requires HOST_PROJECT_DIR env var (set automatically by devcontainer.json).
# For portable setup (containers already running), use scripts/infra/setup.sh --env=dev.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(dirname "$SCRIPTS_ROOT")"

source "$SCRIPTS_ROOT/lib/dc.sh"
init_dc --env=dev

SAMPLE_DATA=""
SKIP_TESTS=false

for arg in "$@"; do
  case "$arg" in
    --with-sample-data) SAMPLE_DATA="--with-sample-data" ;;
    --skip-tests) SKIP_TESTS=true ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# --- Log file setup ---
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/rebuild-$(date '+%Y%m%d-%H%M%S').log"

# Tee all stdout+stderr to the log file while still showing on terminal.
exec > >(tee "$LOG_FILE") 2>&1

# Trap errors so failures are always visible — even when output is piped through
# grep or tail. Without this, a mid-script failure just looks like the output stopped.
trap 'echo ""; echo "=== REBUILD FAILED (exit code $?) ===" >&2; echo "  Log: $LOG_FILE" >&2' ERR

# --- Auto-generate .env if missing ---
if [ ! -f "$PROJECT_DIR/.env" ]; then
  echo "--- Generating .env ---"
  "$SCRIPTS_ROOT/infra/generate-env.sh"
  echo ""
fi

echo "=== Rebuild dev environment ==="
echo "Log file: $LOG_FILE"
echo ""

# --- 1. Tear down existing containers + volumes ---
echo "--- Tearing down existing stack ---"
$DC down -v 2>/dev/null || true
echo "[ok] Stack torn down."
echo ""

# --- 2. Build images ---
echo "--- Building OJS image ---"
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 -f docker/ojs/Dockerfile -t pharkie-ojs-plugins-ojs .
echo "[ok] OJS image built."
echo ""

echo "--- Building WP image ---"
DOCKER_BUILDKIT=1 docker build -f docker/wp/Dockerfile -t pharkie-ojs-plugins-wp .
echo "[ok] WP image built."
echo ""

# --- 3. Bring up the stack ---
echo "--- Starting containers ---"
$DC up -d
echo "[ok] Stack is up."
echo ""

# --- 4. Run setup ---
echo "--- Running setup-dev.sh $SAMPLE_DATA ---"
"$SCRIPTS_ROOT/infra/setup-dev.sh" $SAMPLE_DATA
echo ""

# --- 5. Port forwarding (DinD) ---
echo "--- Setting up port forwarding ---"
"$SCRIPT_DIR/forward-ports.sh"
echo ""

# --- 6. Run tests (unless --skip-tests) ---
if [ "$SKIP_TESTS" = true ]; then
  echo "--- Skipping tests (--skip-tests) ---"
else
  echo "--- Running e2e tests ---"
  npx playwright test
fi
echo ""

# --- 7. Summary ---
echo "=== Rebuild complete ==="
WP_PASS=$($DC exec -T wp printenv WP_ADMIN_PASSWORD 2>/dev/null) || WP_PASS="(check .env)"
OJS_PASS=$($DC exec -T ojs printenv OJS_ADMIN_PASSWORD 2>/dev/null) || OJS_PASS="(check .env)"
echo ""
echo "  WordPress:"
echo "    URL:   http://localhost:8080"
echo "    Admin: http://localhost:8080/wp/wp-admin/"
echo "    Login: admin / $WP_PASS"
echo ""
echo "  OJS:"
echo "    URL:   http://localhost:8081"
echo "    Admin: http://localhost:8081/index.php/ea/management/settings/access"
echo "    Login: admin / $OJS_PASS"
echo ""
echo "  Adminer: http://localhost:8082"
echo "    Server: wp-db (WordPress) or ojs-db (OJS)"
echo ""
echo "  Log: $LOG_FILE"
