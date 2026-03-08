#!/bin/bash
# Unified environment setup: OJS + WP from running containers.
# Waits for services, runs both setup scripts, validates.
#
# Usage:
#   scripts/setup.sh --env=dev                     # Local dev (DinD in devcontainer)
#   scripts/setup.sh --env=staging                  # Staging VPS (sample data on by default)
#   scripts/setup.sh --env=prod                     # Production VPS (no sample data)
#   scripts/setup.sh --env=dev --with-sample-data   # Dev + test data
#
# Compose modes:
#   IP-only (default):  docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d
#   With Caddy/SSL:     docker compose -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.caddy.yml up -d
#
# Requires: containers already running.
set -eo pipefail

# --- Parse arguments ---
ENV=""
SAMPLE_DATA=""
for arg in "$@"; do
  case "$arg" in
    --env=*) ENV="${arg#--env=}" ;;
    --with-sample-data) SAMPLE_DATA="--with-sample-data" ;;
    --no-sample-data) SAMPLE_DATA="" ;;
  esac
done

if [ -z "$ENV" ]; then
  echo "Usage: scripts/setup.sh --env=dev|staging|prod [--with-sample-data]"
  exit 1
fi

# --- Environment-specific compose command and defaults ---
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.staging.yml"

case "$ENV" in
  dev)
    # Docker-in-Docker: volume mounts resolve against the HOST filesystem.
    # --project-directory must be the host path; -f and --env-file are container paths.
    DC="docker compose --project-directory /Users/adamknowles/dev/SEA/wp-ojs-sync -f /workspaces/wp-ojs-sync/docker-compose.yml --env-file /workspaces/wp-ojs-sync/.env"
    ;;
  staging)
    # Detect Caddy overlay: if caddy service is running, include its compose file
    if docker compose $COMPOSE_FILES -f docker-compose.caddy.yml ps --status running 2>/dev/null | grep -q caddy; then
      COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.caddy.yml"
    fi
    DC="docker compose $COMPOSE_FILES"
    # Staging gets sample data by default (for testing)
    [ -z "$SAMPLE_DATA" ] && SAMPLE_DATA="--with-sample-data"
    ;;
  prod)
    if docker compose $COMPOSE_FILES -f docker-compose.caddy.yml ps --status running 2>/dev/null | grep -q caddy; then
      COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.caddy.yml"
    fi
    DC="docker compose $COMPOSE_FILES"
    ;;
  *)
    echo "ERROR: Unknown environment '$ENV'. Use dev, staging, or prod."
    exit 1
    ;;
esac

echo "=== Setup ($ENV) ==="

# --- Verify containers are running ---
for SVC in ojs wp ojs-db wp-db; do
  PS_OUTPUT=$($DC ps --status running 2>/dev/null) || true
  if ! echo "$PS_OUTPUT" | grep -q "$SVC"; then
    echo "ERROR: Container '$SVC' is not running."
    if [ "$ENV" = "dev" ]; then
      echo "Start containers first:  \$DC up -d"
    else
      echo "Start containers first:  docker compose $COMPOSE_FILES up -d"
    fi
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
$DC exec -T ojs bash /scripts/setup-ojs.sh $SAMPLE_DATA

# --- Run WP setup ---
echo ""
echo "=== WP setup ==="
$DC exec -T wp bash /var/www/html/scripts/setup-wp.sh $SAMPLE_DATA

# --- Summary ---
echo ""
echo "=== Setup complete ($ENV) ==="
WP_URL=$($DC exec -T wp printenv WP_HOME 2>/dev/null) || WP_URL="http://localhost:8080"
OJS_URL=$($DC exec -T ojs printenv OJS_BASE_URL 2>/dev/null) || OJS_URL="http://localhost:8081"
JOURNAL_PATH=$($DC exec -T ojs printenv OJS_JOURNAL_PATH 2>/dev/null) || JOURNAL_PATH="journal"
WP_PASS=$($DC exec -T wp printenv WP_ADMIN_PASSWORD 2>/dev/null) || WP_PASS="(check .env)"
OJS_PASS=$($DC exec -T ojs printenv OJS_ADMIN_PASSWORD 2>/dev/null) || OJS_PASS="(check .env)"
echo ""
echo "  WordPress:"
echo "    URL:   $WP_URL"
echo "    Admin: $WP_URL/wp/wp-admin/"
echo "    Login: admin / $WP_PASS"
echo ""
echo "  OJS:"
echo "    URL:   $OJS_URL"
echo "    Admin: $OJS_URL/index.php/$JOURNAL_PATH/management/settings/access"
echo "    Login: admin / $OJS_PASS"

if [ -n "$SAMPLE_DATA" ]; then
  echo ""
  echo "  Next: run bulk sync to push WP members to OJS (~5-10 min):"
  echo "    wp ojs-sync sync --bulk --dry-run    # preview"
  echo "    wp ojs-sync sync --bulk --yes        # do it"
fi
