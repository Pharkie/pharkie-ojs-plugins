#!/bin/bash
# Deploy WP-OJS stack to a VPS.
# Runs FROM the devcontainer (or any machine with SSH access).
#
# Usage:
#   scripts/deploy.sh                          # Deploy to sea-staging, run setup
#   scripts/deploy.sh --host=sea-staging       # Explicit host
#   scripts/deploy.sh --provision              # Install Docker first (fresh VPS)
#   scripts/deploy.sh --skip-setup             # Sync + restart only, no setup
#   scripts/deploy.sh --skip-build             # Don't rebuild images
#   scripts/deploy.sh --ref=some-branch        # Deploy a specific git ref
#   scripts/deploy.sh --clean                  # Tear down volumes first (fresh DB)
#   scripts/deploy.sh --env-file=.env.staging  # Copy local env file to VPS
#
# Prerequisites:
#   - hcloud CLI with active context (resolves server IP automatically)
#   - .env file on VPS (use --env-file on first deploy to copy it)
set -eo pipefail

# --- Parse arguments ---
SSH_HOST="sea-staging"
PROVISION=""
SKIP_SETUP=""
SKIP_BUILD=""
CLEAN=""
GIT_REF="main"
ENV_FILE=""
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
    --provision) PROVISION=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --clean) CLEAN=1 ;;
    --ref=*) GIT_REF="${arg#--ref=}" ;;
    --env-file=*) ENV_FILE="${arg#--env-file=}" ;;
  esac
done

REMOTE_DIR="/opt/wp-ojs-sync"
REPO_URL="https://github.com/Pharkie/wp-ojs-sync.git"
COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.staging.yml"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

source "$SCRIPT_DIR/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

DEPLOY_START=$(date +%s)
phase_time() {
  local now=$(date +%s)
  local elapsed=$(( now - DEPLOY_START ))
  printf "[%dm%02ds]" $((elapsed / 60)) $((elapsed % 60))
}

echo "=== Deploying to $SSH_HOST (ref: $GIT_REF) ==="

# --- Provision (optional) ---
if [ -n "$PROVISION" ]; then
  echo "--- Provisioning VPS ---"
  $SSH_CMD 'bash -s' < "$SCRIPT_DIR/provision-vps.sh"
  echo "$(phase_time) Provisioning done."
  echo ""
fi

# --- Clone or pull repo ---
echo "--- Updating code ---"
$SSH_CMD "
  if [ -d $REMOTE_DIR/.git ]; then
    cd $REMOTE_DIR
    git fetch origin
    git checkout $GIT_REF
    git reset --hard origin/$GIT_REF 2>/dev/null || git reset --hard $GIT_REF
    echo '[ok] Repo updated.'
  else
    echo 'Cloning repo...'
    git clone $REPO_URL ${REMOTE_DIR}.tmp
    # Preserve .env if it exists, then swap
    [ -f $REMOTE_DIR/.env ] && cp $REMOTE_DIR/.env ${REMOTE_DIR}.tmp/.env
    rm -rf $REMOTE_DIR
    mv ${REMOTE_DIR}.tmp $REMOTE_DIR
    cd $REMOTE_DIR
    git checkout $GIT_REF
    echo '[ok] Repo cloned.'
  fi
"

# --- Copy .env file if provided ---
if [ -n "$ENV_FILE" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: Local env file not found: $ENV_FILE"
    exit 1
  fi
  $SCP_CMD "$ENV_FILE" "$SCP_HOST:$REMOTE_DIR/.env"
  echo "[ok] Env file copied to $SCP_HOST:$REMOTE_DIR/.env"
fi

# --- Check .env exists on remote ---
if ! $SSH_CMD "test -f $REMOTE_DIR/.env"; then
  echo ""
  echo "ERROR: No .env file on $SSH_HOST:$REMOTE_DIR/.env"
  echo "Re-run with --env-file to copy it:"
  echo "  scripts/deploy.sh --host=$SSH_HOST --env-file=.env.staging"
  exit 1
fi
# Ensure .env is readable by container processes (scp creates 600 by default, Apache needs to read it)
$SSH_CMD "chmod 644 $REMOTE_DIR/.env"

# Validate required env vars are set (catch blank passwords before compose fails with a cryptic error)
MISSING=$($SSH_CMD "cd $REMOTE_DIR && for VAR in WP_ADMIN_PASSWORD OJS_ADMIN_PASSWORD WP_DB_PASSWORD DB_PASSWORD OJS_DB_PASSWORD WPOJS_API_KEY WPOJS_API_KEY_SECRET; do grep -qE \"^\${VAR}=.+\" .env || echo \$VAR; done")
if [ -n "$MISSING" ]; then
  echo ""
  echo "ERROR: Required variables missing or empty in $SSH_HOST:$REMOTE_DIR/.env:"
  echo "$MISSING" | sed 's/^/  - /'
  echo ""
  echo "Edit the .env file and re-run."
  exit 1
fi

# --- Sync non-git files (paid plugins, import data) ---
# These must exist BEFORE docker compose up, or Docker creates empty directories
# for bind mounts that reference missing files.
echo "--- Syncing non-git files ---"
PAID_PLUGINS="$PROJECT_DIR/wordpress/paid-plugins"
if [ -d "$PAID_PLUGINS" ] && [ "$(ls -A "$PAID_PLUGINS" 2>/dev/null | grep -v README)" ]; then
  rsync -az --exclude='README.md' -e "$RSYNC_SSH" \
    "$PAID_PLUGINS/" "$SCP_HOST:$REMOTE_DIR/wordpress/paid-plugins/"
  echo "[ok] Paid plugins synced."
else
  echo "[skip] No paid plugins found locally."
fi

IMPORT_XML="$PROJECT_DIR/data export/ojs-import-clean.xml"
if [ -f "$IMPORT_XML" ]; then
  $SSH_CMD "mkdir -p '$REMOTE_DIR/data export'"
  rsync -az -e "$RSYNC_SSH" \
    "$IMPORT_XML" "$SCP_HOST:$REMOTE_DIR/data export/ojs-import-clean.xml"
  echo "[ok] OJS import XML synced."
else
  echo "[skip] No OJS import XML found locally."
fi

# --- Clean (optional: tear down volumes for fresh DB) ---
if [ -n "$CLEAN" ]; then
  echo "--- Cleaning: removing containers + volumes ---"
  $SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD down -v 2>/dev/null || true"
  echo "$(phase_time) Clean slate."
fi

# --- Build images ---
if [ -z "$SKIP_BUILD" ]; then
  echo "--- Building images ---"
  $SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD build"
  echo "$(phase_time) Images built."
fi

# --- Stop existing containers (prevents "name already in use" conflicts) ---
echo "--- Stopping existing containers ---"
$SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD down 2>/dev/null || true"

# --- Start stack ---
echo "--- Starting stack ---"
$SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD up -d"
echo "$(phase_time) Stack is up."

# --- Run setup ---
if [ -z "$SKIP_SETUP" ]; then
  echo "--- Running setup ---"
  $SSH_CMD "cd $REMOTE_DIR && bash scripts/setup.sh --env=staging"
  echo "$(phase_time) Setup complete."
fi

DEPLOY_END=$(date +%s)
DEPLOY_ELAPSED=$(( DEPLOY_END - DEPLOY_START ))
echo ""
echo "=== Deploy complete ($(( DEPLOY_ELAPSED / 60 ))m $(( DEPLOY_ELAPSED % 60 ))s) ==="
$SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'"
