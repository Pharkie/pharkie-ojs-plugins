#!/bin/bash
# Unified environment setup: OJS + WP from running containers.
# Waits for services, runs both setup scripts, validates.
#
# Usage:
#   scripts/infra/setup.sh --env=dev                     # Local dev (DinD in devcontainer)
#   scripts/infra/setup.sh --env=staging                  # Staging VPS (sample data on by default)
#   scripts/infra/setup.sh --env=prod                     # Production VPS (no sample data)
#   scripts/infra/setup.sh --env=dev --with-sample-data   # Dev + test data
#
# Compose modes:
#   Server:  docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d
#   (TLS comes from the box's shared Caddy, its own stack; see docs/shared-caddy.md.)
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
  echo "Usage: scripts/infra/setup.sh --env=dev|staging|prod [--with-sample-data]"
  exit 1
fi

# --- Environment-specific compose command and defaults ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(dirname "$SCRIPTS_ROOT")"

source "$SCRIPTS_ROOT/lib/dc.sh"
init_dc --env="$ENV"

## Sample data is always opt-in — pass --with-sample-data explicitly.

# --- Auto-generate .env if missing (dev only) ---
if [ "$ENV" = "dev" ] && [ ! -f "$PROJECT_DIR/.env" ]; then
  echo "--- Generating .env ---"
  "$SCRIPT_DIR/generate-env.sh"
  echo ""
fi

echo "=== Setup ($ENV) ==="

# --- Is the WordPress test rig here? ---
# On a server it runs only with the wp-rig profile (docker-compose.staging.yml);
# without it this sets up the journal alone. Local dev always has it.
WP_RIG=1
if [ "$ENV" != "dev" ] && ! $DC ps --services --status running 2>/dev/null | grep -qx wp; then
  WP_RIG=""
  echo "[skip] WordPress test rig not running (profile wp-rig): journal only."
fi
SERVICES="ojs ojs-db"
[ -n "$WP_RIG" ] && SERVICES="ojs wp ojs-db wp-db"

# --- Verify containers are running ---
for SVC in $SERVICES; do
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
echo "[ok] Containers running: $SERVICES."

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
# Container paths differ: OJS mounts ./scripts at /scripts (container root),
# WP mounts at /var/www/html/scripts (Bedrock web root). See docker-compose.yml volumes.
echo ""
echo "=== OJS setup ==="
$DC exec -T ojs bash /scripts/ojs/setup-ojs.sh $SAMPLE_DATA

# --- Assign editorial roles (prod/staging only) ---
if [ "$ENV" != "dev" ]; then
  echo ""
  echo "=== Editorial roles ==="
  $DC exec -T ojs bash /scripts/ojs/assign-roles.sh
fi

# --- Run WP setup ---
if [ -n "$WP_RIG" ]; then
  echo ""
  echo "=== WP setup ==="
  $DC exec -T wp bash /var/www/html/scripts/wp/setup-wp.sh $SAMPLE_DATA
fi

# --- Summary ---
echo ""
echo "=== Setup complete ($ENV) ==="
WP_URL=$($DC exec -T wp printenv WP_HOME 2>/dev/null) || WP_URL="http://localhost:8080"
OJS_URL=$($DC exec -T ojs printenv OJS_BASE_URL 2>/dev/null) || OJS_URL="http://localhost:8081"
JOURNAL_PATH=$($DC exec -T ojs printenv OJS_JOURNAL_PATH 2>/dev/null) || JOURNAL_PATH="ea"
WP_PASS=$($DC exec -T wp printenv WP_ADMIN_PASSWORD 2>/dev/null) || WP_PASS="(check .env)"
OJS_PASS=$($DC exec -T ojs printenv OJS_ADMIN_PASSWORD 2>/dev/null) || OJS_PASS="(check .env)"
if [ -n "$WP_RIG" ]; then
  echo ""
  echo "  WordPress:"
  echo "    URL:   $WP_URL"
  echo "    Admin: $WP_URL/wp/wp-admin/"
  echo "    Login: admin / $WP_PASS"
fi
echo ""
echo "  OJS:"
echo "    URL:   $OJS_URL"
echo "    Admin: $OJS_URL/index.php/$JOURNAL_PATH/management/settings/access"
echo "    Login: admin / $OJS_PASS"
echo ""
WP_DB_PASS=$($DC exec -T wp printenv WORDPRESS_DB_PASSWORD 2>/dev/null) || WP_DB_PASS="(check .env)"
OJS_DB_PASS=$($DC exec -T ojs printenv OJS_DB_PASSWORD 2>/dev/null) || OJS_DB_PASS="(check .env)"
echo "  Adminer (DB admin):"
if [ "$ENV" = "dev" ]; then
  echo "    URL:      http://localhost:8082"
else
  echo "    Access:   ssh -L 8082:127.0.0.1:8082 <host>  →  http://localhost:8082"
fi
[ -n "$WP_RIG" ] && echo "    WP DB:    server=wp-db   user=wordpress  password=$WP_DB_PASS"
echo "    OJS DB:   server=ojs-db  user=ojs        password=$OJS_DB_PASS"

if [ -n "$SAMPLE_DATA" ] && [ -n "$WP_RIG" ]; then
  echo ""
  echo "  Next: run bulk sync to push WP members to OJS (~5-10 min):"
  echo "    wp ojs-sync sync --bulk --dry-run    # preview"
  echo "    wp ojs-sync sync --bulk --yes        # do it"
fi
