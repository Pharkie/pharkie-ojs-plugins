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
#
# Prerequisites:
#   - SSH access configured (Host entry in ~/.ssh/config)
#   - Deploy key on VPS with access to the GitHub repo
#   - .env file on VPS at /opt/wp-ojs-sync/.env
set -eo pipefail

# --- Parse arguments ---
SSH_HOST="sea-staging"
PROVISION=""
SKIP_SETUP=""
SKIP_BUILD=""
GIT_REF="main"
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
    --provision) PROVISION=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --ref=*) GIT_REF="${arg#--ref=}" ;;
  esac
done

REMOTE_DIR="/opt/wp-ojs-sync"
REPO_URL="git@github.com:Pharkie/wp-ojs-sync.git"
COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.staging.yml"
SSH_CMD="ssh -o ConnectTimeout=10 $SSH_HOST"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Deploying to $SSH_HOST (ref: $GIT_REF) ==="

# --- Provision (optional) ---
if [ -n "$PROVISION" ]; then
  echo "--- Provisioning VPS ---"
  $SSH_CMD 'bash -s' < "$SCRIPT_DIR/provision-vps.sh"
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

# --- Check .env exists on remote ---
if ! $SSH_CMD "test -f $REMOTE_DIR/.env"; then
  echo ""
  echo "ERROR: No .env file on $SSH_HOST:$REMOTE_DIR/.env"
  echo "Copy the staging env file first:"
  echo "  scp .env.staging $SSH_HOST:$REMOTE_DIR/.env"
  exit 1
fi
# Ensure .env is readable by container processes (scp creates 600 by default)
$SSH_CMD "chmod 644 $REMOTE_DIR/.env"

# --- Build images ---
if [ -z "$SKIP_BUILD" ]; then
  echo "--- Building images ---"
  $SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD build"
  echo "[ok] Images built."
fi

# --- Start/restart stack ---
echo "--- Starting stack ---"
$SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD up -d"
echo "[ok] Stack is up."

# --- Run setup ---
if [ -z "$SKIP_SETUP" ]; then
  echo "--- Running setup ---"
  $SSH_CMD "cd $REMOTE_DIR && bash scripts/setup.sh --env=staging"
fi

echo ""
echo "=== Deploy complete ==="
$SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'"
