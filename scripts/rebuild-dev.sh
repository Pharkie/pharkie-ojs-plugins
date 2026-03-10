#!/bin/bash
# Full nuke-and-pave for the dev environment (devcontainer only).
# Tears down everything, rebuilds images, brings up the stack,
# runs setup, and optionally runs the e2e test suite.
#
# Usage:
#   scripts/rebuild-dev.sh                          # Rebuild + tests
#   scripts/rebuild-dev.sh --with-sample-data       # Rebuild + sample data + tests
#   scripts/rebuild-dev.sh --skip-tests             # Rebuild without tests
#   scripts/rebuild-dev.sh --with-sample-data --skip-tests
#
# Output is always tee'd to logs/rebuild-<timestamp>.log so it's recoverable.
#
# This script is devcontainer-specific (hardcoded host path for DinD).
# For portable setup (containers already running), use scripts/setup.sh --env=dev.
set -eo pipefail

DC="docker compose --project-directory /Users/adamknowles/dev/SEA/wp-ojs-sync -f /workspaces/wp-ojs-sync/docker-compose.yml --env-file /workspaces/wp-ojs-sync/.env"

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
LOG_DIR="/workspaces/wp-ojs-sync/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/rebuild-$(date '+%Y%m%d-%H%M%S').log"

# Tee all stdout+stderr to the log file while still showing on terminal.
# Use a file descriptor so we can report the log path on exit.
exec > >(tee "$LOG_FILE") 2>&1

# --- Auto-generate .env if missing ---
if [ ! -f /workspaces/wp-ojs-sync/.env ]; then
  echo "--- Generating .env ---"
  /workspaces/wp-ojs-sync/scripts/generate-env.sh
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
DOCKER_BUILDKIT=1 docker build --platform linux/amd64 -f docker/ojs/Dockerfile -t wp-ojs-sync-ojs .
echo "[ok] OJS image built."
echo ""

echo "--- Building WP image ---"
DOCKER_BUILDKIT=1 docker build -f docker/wp/Dockerfile -t wp-ojs-sync-wp .
echo "[ok] WP image built."
echo ""

# --- 3. Bring up the stack ---
echo "--- Starting containers ---"
$DC up -d
echo "[ok] Stack is up."
echo ""

# --- 4. Run setup ---
echo "--- Running setup-dev.sh $SAMPLE_DATA ---"
scripts/setup-dev.sh $SAMPLE_DATA
echo ""

# --- 5. Prepare for tests ---
# Kill stale socat forwarders from a previous run (they hold the ports but
# point at containers that no longer exist after the teardown+rebuild).
echo "--- Preparing test infrastructure ---"
# [s] trick prevents pkill from matching its own command line
pkill -f '[s]ocat.*TCP-LISTEN' 2>/dev/null || true
sleep 1  # Let killed processes release their ports

# Connect the devcontainer to the compose network so hostname resolution
# (wp, ojs) works for socat and direct connectivity checks.
NETWORK="wp-ojs-sync_sea-net"
CONTAINER_ID=$(cat /etc/hostname)
if ! docker network inspect "$NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}' 2>/dev/null | grep -q "$CONTAINER_ID"; then
  echo "Connecting devcontainer to $NETWORK..."
  docker network connect "$NETWORK" "$CONTAINER_ID" 2>/dev/null || true
fi

# Start socat forwarders and verify they work. Playwright's global-setup also
# does this, but it spawns socat with stdio:ignore so errors are invisible.
# Doing it here lets us catch failures with full error output.
for FORWARD in "8080:wp:80" "8081:ojs:80" "8082:adminer:8080"; do
  LOCAL_PORT="${FORWARD%%:*}"
  REMOTE="${FORWARD#*:}"
  REMOTE_HOST="${REMOTE%%:*}"
  REMOTE_PORT="${REMOTE#*:}"

  # Skip if already listening (from a surviving forwarder)
  if bash -c "echo >/dev/tcp/localhost/$LOCAL_PORT" 2>/dev/null; then
    echo "[ok] localhost:$LOCAL_PORT already forwarding."
    continue
  fi

  socat "TCP-LISTEN:$LOCAL_PORT,fork,reuseaddr" "TCP:$REMOTE_HOST:$REMOTE_PORT" 2>/dev/null &
  SOCAT_PID=$!

  # Wait up to 5s for it to start listening
  for i in $(seq 1 10); do
    if bash -c "echo >/dev/tcp/localhost/$LOCAL_PORT" 2>/dev/null; then
      echo "[ok] localhost:$LOCAL_PORT → $REMOTE_HOST:$REMOTE_PORT (pid $SOCAT_PID)"
      break
    fi
    if [ "$i" = "10" ]; then
      echo "ERROR: socat failed to forward localhost:$LOCAL_PORT → $REMOTE_HOST:$REMOTE_PORT"
      exit 1
    fi
    sleep 0.5
  done
done
echo "[ok] Test infrastructure ready."
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
