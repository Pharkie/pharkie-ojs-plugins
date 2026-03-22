#!/bin/bash
# Pull OJS database backups from the live VPS off-server.
# Runs FROM the devcontainer (or any machine with SSH access to sea-live).
#
# Usage:
#   scripts/pull-ojs-backup.sh                     # Pull latest daily backup
#   scripts/pull-ojs-backup.sh --all               # Pull all backups (daily + weekly)
#   scripts/pull-ojs-backup.sh --list              # List available backups on VPS
#   scripts/pull-ojs-backup.sh --install-cron      # Install daily cron job on VPS
#   scripts/pull-ojs-backup.sh --remove-cron       # Remove cron job from VPS
#   scripts/pull-ojs-backup.sh --decrypt=FILE      # Decrypt a pulled backup (needs --key=)
#   scripts/pull-ojs-backup.sh --host=sea-live     # Explicit SSH host (default: sea-live)
#   scripts/pull-ojs-backup.sh --dest=./backups    # Local destination (default: backups/ojs/)
#   scripts/pull-ojs-backup.sh --key=/path/to/key  # Encryption key file (for --decrypt)
#
# Backups are AES-256-CBC encrypted on the VPS. To restore:
#   scripts/pull-ojs-backup.sh --decrypt=backups/ojs/ojs-20260322.sql.gz.enc --key=/path/to/.backup-key
#   # produces ojs-20260322.sql.gz → pipe to: gunzip | mariadb -u root -p"$PASS" ojs
set -eo pipefail

SSH_HOST="sea-live"
LOCAL_DEST="backups/ojs"
MODE="pull-latest"
DECRYPT_FILE=""
KEY_FILE=""

for arg in "$@"; do
  case "$arg" in
    --all) MODE="pull-all" ;;
    --list) MODE="list" ;;
    --install-cron) MODE="install-cron" ;;
    --remove-cron) MODE="remove-cron" ;;
    --decrypt=*) MODE="decrypt"; DECRYPT_FILE="${arg#--decrypt=}" ;;
    --host=*) SSH_HOST="${arg#--host=}" ;;
    --dest=*) LOCAL_DEST="${arg#--dest=}" ;;
    --key=*) KEY_FILE="${arg#--key=}" ;;
  esac
done

REMOTE_BACKUP_DIR="/opt/backups/ojs"
REMOTE_PROJECT_DIR="/opt/wp-ojs-sync"
CRON_SCHEDULE="0 3 * * *"
CRON_CMD="$REMOTE_PROJECT_DIR/scripts/backup-ojs-db.sh >> $REMOTE_BACKUP_DIR/backup.log 2>&1"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

case "$MODE" in
  list)
    log "Listing backups on $SSH_HOST..."
    echo ""
    echo "=== Daily ==="
    ssh "$SSH_HOST" "ls -lhtr $REMOTE_BACKUP_DIR/daily/ojs-*.sql.gz.enc 2>/dev/null || echo '  (none)'"
    echo ""
    echo "=== Weekly ==="
    ssh "$SSH_HOST" "ls -lhtr $REMOTE_BACKUP_DIR/weekly/ojs-*.sql.gz.enc 2>/dev/null || echo '  (none)'"
    echo ""
    echo "=== Last 5 log lines ==="
    ssh "$SSH_HOST" "tail -5 $REMOTE_BACKUP_DIR/backup.log 2>/dev/null || echo '  (no log yet)'"
    ;;

  pull-latest)
    log "Pulling latest backup from $SSH_HOST..."
    mkdir -p "$LOCAL_DEST"
    LATEST=$(ssh "$SSH_HOST" "ls -t $REMOTE_BACKUP_DIR/daily/ojs-*.sql.gz.enc 2>/dev/null | head -1")
    if [ -z "$LATEST" ]; then
      log "ERROR: No backups found on $SSH_HOST"
      exit 1
    fi
    FILENAME=$(basename "$LATEST")
    if [ -f "$LOCAL_DEST/$FILENAME" ]; then
      log "Already have $FILENAME locally, skipping"
    else
      scp "$SSH_HOST:$LATEST" "$LOCAL_DEST/"
      log "Pulled: $FILENAME -> $LOCAL_DEST/$FILENAME"
    fi
    SIZE=$(stat -c%s "$LOCAL_DEST/$FILENAME" 2>/dev/null || stat -f%z "$LOCAL_DEST/$FILENAME" 2>/dev/null)
    log "Size: $(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE}B")"
    ;;

  pull-all)
    log "Pulling all backups from $SSH_HOST..."
    mkdir -p "$LOCAL_DEST/daily" "$LOCAL_DEST/weekly"
    rsync -avz --progress -e ssh \
      "$SSH_HOST:$REMOTE_BACKUP_DIR/daily/" "$LOCAL_DEST/daily/" || { log "ERROR: rsync daily failed"; exit 1; }
    rsync -avz --progress -e ssh \
      "$SSH_HOST:$REMOTE_BACKUP_DIR/weekly/" "$LOCAL_DEST/weekly/" || { log "ERROR: rsync weekly failed"; exit 1; }
    log "Sync complete"
    ls -lhR "$LOCAL_DEST"
    ;;

  decrypt)
    if [ -z "$DECRYPT_FILE" ]; then
      log "ERROR: --decrypt requires a file path"
      exit 1
    fi
    if [ ! -f "$DECRYPT_FILE" ]; then
      log "ERROR: File not found: $DECRYPT_FILE"
      exit 1
    fi
    if [ -z "$KEY_FILE" ]; then
      log "ERROR: --decrypt requires --key=/path/to/.backup-key"
      exit 1
    fi
    if [ ! -f "$KEY_FILE" ]; then
      log "ERROR: Key file not found: $KEY_FILE"
      exit 1
    fi
    OUTPUT="${DECRYPT_FILE%.enc}"
    log "Decrypting $DECRYPT_FILE..."
    openssl enc -aes-256-cbc -d -pbkdf2 -pass file:"$KEY_FILE" -in "$DECRYPT_FILE" -out "$OUTPUT"
    # Verify gzip integrity
    if gzip -t "$OUTPUT" 2>/dev/null; then
      SIZE=$(stat -c%s "$OUTPUT" 2>/dev/null || stat -f%z "$OUTPUT" 2>/dev/null)
      log "Decrypted: $OUTPUT ($(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE}B"), gzip OK)"
      log "To restore: gunzip -c $OUTPUT | mariadb -u root -p\"\$PASS\" ojs"
    else
      log "ERROR: Decrypted file failed gzip integrity check"
      rm -f "$OUTPUT"
      exit 1
    fi
    ;;

  install-cron)
    log "Installing backup cron job on $SSH_HOST..."

    # Ensure backup dir exists
    ssh "$SSH_HOST" "mkdir -p $REMOTE_BACKUP_DIR/daily $REMOTE_BACKUP_DIR/weekly"

    # Make backup script executable
    ssh "$SSH_HOST" "chmod +x $REMOTE_PROJECT_DIR/scripts/backup-ojs-db.sh"

    # Check if cron already installed
    EXISTING=$(ssh "$SSH_HOST" "crontab -l 2>/dev/null | grep -F 'backup-ojs-db.sh' || true")
    if [ -n "$EXISTING" ]; then
      log "Cron job already installed:"
      echo "  $EXISTING"
      exit 0
    fi

    # Add to crontab (preserve existing entries)
    ssh "$SSH_HOST" "( crontab -l 2>/dev/null; echo '$CRON_SCHEDULE $CRON_CMD' ) | crontab -"

    log "Cron job installed: daily at 03:00 UTC"
    log "Verify with: ssh $SSH_HOST crontab -l"

    # Run a test backup now
    log "Running first backup now..."
    ssh "$SSH_HOST" "$REMOTE_PROJECT_DIR/scripts/backup-ojs-db.sh"
    log "First backup complete. Check with: $0 --list"
    ;;

  remove-cron)
    log "Removing backup cron job from $SSH_HOST..."
    ssh "$SSH_HOST" "crontab -l 2>/dev/null | grep -v 'backup-ojs-db.sh' | crontab - 2>/dev/null || true"
    log "Cron job removed"
    ;;
esac
