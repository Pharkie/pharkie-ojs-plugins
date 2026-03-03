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
# This script is devcontainer-specific (hardcoded host path for DinD).
# For portable setup (containers already running), use scripts/setup-dev.sh.
set -e

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

echo "=== Rebuild dev environment ==="
echo ""

# --- 1. Tear down existing containers + volumes ---
echo "--- Tearing down existing stack ---"
$DC down -v 2>/dev/null || true
echo "[ok] Stack torn down."
echo ""

# --- 2. Build images ---
echo "--- Building OJS image ---"
DOCKER_BUILDKIT=1 docker build -f docker/ojs/Dockerfile -t wp-ojs-sync-ojs .
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

# --- 5. Run tests (unless --skip-tests) ---
if [ "$SKIP_TESTS" = true ]; then
  echo "--- Skipping tests (--skip-tests) ---"
else
  echo "--- Running e2e tests ---"
  npx playwright test
fi
echo ""

# --- 6. Summary ---
echo "=== Rebuild complete ==="
echo "  WP:  http://localhost:8080  (admin / admin123)"
echo "  OJS: http://localhost:8081  (admin / admin123)"
