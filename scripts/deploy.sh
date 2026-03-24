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
#   scripts/deploy.sh --ssl                    # Enable Caddy reverse proxy with automatic Let's Encrypt SSL
#   scripts/deploy.sh --with-sample-data       # Include test users/subscriptions in setup (dev/staging only!)
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
SSL=""
GIT_REF="main"
ENV_FILE=""
SAMPLE_DATA=""
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
    --provision) PROVISION=1 ;;
    --skip-setup) SKIP_SETUP=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --clean) CLEAN=1 ;;
    --ssl) SSL=1 ;;
    --ref=*) GIT_REF="${arg#--ref=}" ;;
    --env-file=*) ENV_FILE="${arg#--env-file=}" ;;
    --with-sample-data) SAMPLE_DATA="--with-sample-data" ;;
  esac
done

REMOTE_DIR="/opt/pharkie-ojs-plugins"
REPO_URL="https://github.com/Pharkie/pharkie-ojs-plugins.git"
COMPOSE_CMD="docker compose -f docker-compose.yml -f docker-compose.staging.yml"
if [ -n "$SSL" ]; then
  COMPOSE_CMD="$COMPOSE_CMD -f docker-compose.caddy.yml"
fi
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

# --- Deploy lock ---
LOCK_FILE="/tmp/pharkie-ojs-plugins-deploy.lock"
LOCK_INFO=$($SSH_CMD "
  if [ -f $LOCK_FILE ]; then
    lock_age=\$(( \$(date +%s) - \$(stat -c %Y $LOCK_FILE 2>/dev/null || echo 0) ))
    if [ \$lock_age -lt 1800 ]; then
      cat $LOCK_FILE
      exit 1
    else
      echo 'STALE'
    fi
  fi
" 2>&1) || {
  echo "ERROR: Another deploy is in progress (started by: $LOCK_INFO)"
  echo "If this is stale, wait 30 minutes or remove $LOCK_FILE on the server."
  exit 1
}
if [ "$LOCK_INFO" = "STALE" ]; then
  echo "WARNING: Stale deploy lock found (>30 min old), removing."
fi
$SSH_CMD "echo '$(whoami)@$(hostname) at $(date -Iseconds)' > $LOCK_FILE"
# Remove lock on exit (success or failure)
trap '$SSH_CMD "rm -f $LOCK_FILE" 2>/dev/null' EXIT

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
  # Auto-decrypt SOPS-encrypted env files
  if head -5 "$ENV_FILE" | grep -q '"sops"\|ENC\[AES256'; then
    echo "Decrypting SOPS-encrypted env file..."
    DECRYPTED=$(mktemp)
    if ! sops -d "$ENV_FILE" > "$DECRYPTED"; then
      echo "ERROR: Failed to decrypt $ENV_FILE. Is the age key at ~/.config/sops/age/keys.txt?"
      rm -f "$DECRYPTED"
      exit 1
    fi
    $SCP_CMD "$DECRYPTED" "$SCP_HOST:$REMOTE_DIR/.env"
    rm -f "$DECRYPTED"
  else
    $SCP_CMD "$ENV_FILE" "$SCP_HOST:$REMOTE_DIR/.env"
  fi
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
MISSING=$($SSH_CMD "cd $REMOTE_DIR && for VAR in WP_ADMIN_PASSWORD OJS_ADMIN_PASSWORD WP_DB_PASSWORD DB_PASSWORD OJS_DB_PASSWORD WPOJS_API_KEY WPOJS_API_KEY_SECRET OJS_API_KEY_SECRET; do grep -qE \"^\${VAR}=.+\" .env || echo \$VAR; done")
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

THEMES="$PROJECT_DIR/wordpress/themes"
if [ -d "$THEMES" ] && [ "$(ls -A "$THEMES" 2>/dev/null | grep -v README)" ]; then
  $SSH_CMD "mkdir -p '$REMOTE_DIR/wordpress/web/app/themes'"
  rsync -az --exclude='README.md' -e "$RSYNC_SSH" \
    "$THEMES/" "$SCP_HOST:$REMOTE_DIR/wordpress/web/app/themes/"
  echo "[ok] Themes synced."
else
  echo "[skip] No custom themes found locally."
fi

# Sync sample issue XMLs for staging (backfill-generated, with correct per-article galleys)
SAMPLE_ISSUES=("$PROJECT_DIR/backfill/output/35.2/import.xml" "$PROJECT_DIR/backfill/output/36.1/import.xml")
HAVE_SAMPLES=false
for f in "${SAMPLE_ISSUES[@]}"; do
  [ -f "$f" ] && HAVE_SAMPLES=true
done
if [ "$HAVE_SAMPLES" = true ]; then
  $SSH_CMD "mkdir -p '$REMOTE_DIR/backfill/output/35.2' '$REMOTE_DIR/backfill/output/36.1'"
  for f in "${SAMPLE_ISSUES[@]}"; do
    if [ -f "$f" ]; then
      RELPATH="${f#$PROJECT_DIR/}"
      rsync -az -e "$RSYNC_SSH" "$f" "$SCP_HOST:$REMOTE_DIR/$RELPATH"
    fi
  done
  echo "[ok] Sample issue XMLs synced."
else
  echo "[skip] No sample issue XMLs found locally."
fi

# Sync editorial roles mapping
ROLES_FILE="$PROJECT_DIR/private/editorial-roles.json"
if [ -f "$ROLES_FILE" ]; then
  $SSH_CMD "mkdir -p '$REMOTE_DIR/private'"
  rsync -az -e "$RSYNC_SSH" "$ROLES_FILE" "$SCP_HOST:$REMOTE_DIR/private/editorial-roles.json"
  echo "[ok] Editorial roles synced."
else
  echo "[skip] No editorial-roles.json found locally."
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

# --- Pre-deploy database snapshot (skip on --clean, volumes are wiped anyway) ---
if [ -z "$CLEAN" ]; then
  echo "--- Snapshotting databases ---"
  SNAP_TS=$(date +%s)
  $SSH_CMD "cd $REMOTE_DIR && {
    $COMPOSE_CMD exec -T ojs-db bash -c 'mysqldump -u root -p\$MYSQL_ROOT_PASSWORD \$MYSQL_DATABASE 2>/dev/null' > /tmp/pre-deploy-ojs-$SNAP_TS.sql && \
    $COMPOSE_CMD exec -T wp-db bash -c 'mysqldump -u root -p\$MYSQL_ROOT_PASSWORD \$MYSQL_DATABASE 2>/dev/null' > /tmp/pre-deploy-wp-$SNAP_TS.sql && \
    chmod 600 /tmp/pre-deploy-ojs-$SNAP_TS.sql /tmp/pre-deploy-wp-$SNAP_TS.sql && \
    echo '[ok] Snapshots: /tmp/pre-deploy-{ojs,wp}-$SNAP_TS.sql'
    # Keep last 3 snapshots per DB, delete older ones
    ls -t /tmp/pre-deploy-ojs-*.sql 2>/dev/null | tail -n +4 | xargs rm -f 2>/dev/null
    ls -t /tmp/pre-deploy-wp-*.sql 2>/dev/null | tail -n +4 | xargs rm -f 2>/dev/null
  } 2>/dev/null || echo '[skip] DB snapshot failed (containers not running?), continuing.'"
  echo "$(phase_time) Snapshot done."
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
  $SSH_CMD "cd $REMOTE_DIR && bash scripts/setup.sh --env=staging $SAMPLE_DATA"
  echo "$(phase_time) Setup complete."
fi

# --- Ensure backup cron is installed ---
echo "--- Checking backup cron ---"
$SSH_CMD "
  chmod +x $REMOTE_DIR/scripts/backup-ojs-db.sh 2>/dev/null
  if crontab -l 2>/dev/null | grep -qF 'backup-ojs-db.sh'; then
    echo '[ok] Backup cron already installed.'
  elif [ -f $REMOTE_DIR/scripts/backup-ojs-db.sh ]; then
    mkdir -p /opt/backups/ojs/daily /opt/backups/ojs/weekly
    (crontab -l 2>/dev/null; echo '0 3 * * * /opt/pharkie-ojs-plugins/scripts/backup-ojs-db.sh >> /opt/backups/ojs/backup.log 2>&1') | crontab -
    echo '[ok] Backup cron installed (daily 03:00 UTC).'
  else
    echo '[skip] backup-ojs-db.sh not found, skipping cron.'
  fi
"

DEPLOY_END=$(date +%s)
DEPLOY_ELAPSED=$(( DEPLOY_END - DEPLOY_START ))
echo ""
echo "=== Deploy complete ($(( DEPLOY_ELAPSED / 60 ))m $(( DEPLOY_ELAPSED % 60 ))s) ==="
$SSH_CMD "cd $REMOTE_DIR && $COMPOSE_CMD ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'"
