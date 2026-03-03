#!/bin/bash
# Full dev environment setup: OJS + WP from clean containers.
# Waits for services, runs both setup scripts in order, validates.
#
# Usage (from devcontainer):
#   scripts/setup-dev.sh                    # Base setup
#   scripts/setup-dev.sh --with-sample-data # Base + test data
#
# Requires: containers already running ($DC up -d).
set -e

DC="docker compose --project-directory /Users/adamknowles/dev/SEA/wp-ojs-sync -f /workspaces/wp-ojs-sync/docker-compose.yml --env-file /workspaces/wp-ojs-sync/.env"

ARGS=""
for arg in "$@"; do
  case "$arg" in
    --with-sample-data) ARGS="--with-sample-data" ;;
  esac
done

echo "=== Dev environment setup ==="

# --- Verify containers are running ---
for SVC in ojs wp ojs-db wp-db; do
  if ! $DC ps --status running 2>/dev/null | grep -q "$SVC"; then
    echo "ERROR: Container '$SVC' is not running."
    echo "Start containers first:  \$DC up -d"
    exit 1
  fi
done
echo "[ok] All 4 containers running."

# --- Wait for OJS to be ready (entrypoint install may still be running) ---
echo "Waiting for OJS HTTP..."
for i in $(seq 1 30); do
  if $DC exec -T ojs curl -sf http://localhost:80/ -o /dev/null 2>/dev/null; then
    break
  fi
  if [ "$i" = "30" ]; then
    echo "ERROR: OJS not responding after 60s."
    exit 1
  fi
  sleep 2
done
echo "[ok] OJS responding."

# --- Run OJS setup ---
echo ""
echo "=== OJS setup ==="
$DC exec ojs bash /scripts/setup-ojs.sh $ARGS

# --- Run WP setup ---
echo ""
echo "=== WP setup ==="
$DC exec wp bash /var/www/html/scripts/setup-wp.sh $ARGS

echo ""
echo "=== Setup complete ==="
echo "  WP:  http://localhost:8080  (admin / admin123)"
echo "  OJS: http://localhost:8081  (admin / admin123)"
