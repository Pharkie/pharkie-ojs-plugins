#!/bin/bash
# Automated OJS database backup with encryption and rotation.
# Runs ON the VPS (called by cron or manually).
#
# Usage:
#   scripts/backup-ojs-db.sh              # Dump + compress + encrypt + rotate
#   scripts/backup-ojs-db.sh --dry-run    # Show what would happen
#
# Output: AES-256-CBC encrypted gzip files (.sql.gz.enc)
# Encryption key: /opt/backups/ojs/.backup-key (create once, back up separately)
#
# Retention: 7 daily + 4 weekly (Sunday dumps promoted).
# Backups stored in /opt/backups/ojs/
#
# Cron (installed by --install-cron from pull-ojs-backup.sh):
#   0 3 * * * /opt/pharkie-ojs-plugins/scripts/backup-ojs-db.sh >> /opt/backups/ojs/backup.log 2>&1
#
# To restore:
#   openssl enc -aes-256-cbc -d -pbkdf2 -in backup.sql.gz.enc -pass file:/path/to/.backup-key | gunzip | mariadb -u root -p"$PASS" ojs
set -eo pipefail

BACKUP_DIR="/opt/backups/ojs"
DAILY_DIR="$BACKUP_DIR/daily"
WEEKLY_DIR="$BACKUP_DIR/weekly"
PROJECT_DIR="/opt/pharkie-ojs-plugins"
KEY_FILE="$BACKUP_DIR/.backup-key"
KEEP_DAILY=7
KEEP_WEEKLY=4
DRY_RUN=""

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
  esac
done

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

# --- Load credentials ---
ENV_FILE="$PROJECT_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  log "ERROR: $ENV_FILE not found"
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

if [ -z "$OJS_DB_PASSWORD" ]; then
  log "ERROR: OJS_DB_PASSWORD not set in $ENV_FILE"
  exit 1
fi

# --- Check encryption key ---
if [ ! -f "$KEY_FILE" ]; then
  log "ERROR: Encryption key not found at $KEY_FILE"
  log "Create one with: openssl rand -base64 32 > $KEY_FILE && chmod 600 $KEY_FILE"
  exit 1
fi

# --- Create directories ---
mkdir -p "$DAILY_DIR" "$WEEKLY_DIR"

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
DAY_OF_WEEK=$(date +%u)  # 1=Monday, 7=Sunday
DUMP_FILE="$DAILY_DIR/ojs-$TIMESTAMP.sql.gz.enc"

if [ -n "$DRY_RUN" ]; then
  log "DRY RUN: would dump to $DUMP_FILE"
  log "DRY RUN: would keep $KEEP_DAILY daily, $KEEP_WEEKLY weekly"
  log "DRY RUN: today is day-of-week $DAY_OF_WEEK (7=Sunday, would promote to weekly)"
  exit 0
fi

# --- Pre-flight: check Docker is running ---
if ! docker compose -f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.staging.yml" \
  ps --status running 2>/dev/null | grep -q ojs-db; then
  log "ERROR: ojs-db container is not running"
  exit 1
fi

# --- Dump + compress + encrypt (atomic: write to .tmp, rename on success) ---
log "Starting OJS database backup..."
START=$(date +%s)
TMP_FILE="${DUMP_FILE}.tmp"

docker compose -f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.staging.yml" \
  exec -T ojs-db mariadb-dump \
  --single-transaction \
  --routines \
  --triggers \
  -u "${OJS_DB_USER:-ojs}" -p"$OJS_DB_PASSWORD" "${OJS_DB_NAME:-ojs}" \
  | gzip \
  | openssl enc -aes-256-cbc -pbkdf2 -pass file:"$KEY_FILE" -out "$TMP_FILE"

DUMP_SIZE=$(stat -c%s "$TMP_FILE" 2>/dev/null || stat -f%z "$TMP_FILE" 2>/dev/null)
ELAPSED=$(( $(date +%s) - START ))
log "Dump complete: $(numfmt --to=iec "$DUMP_SIZE" 2>/dev/null || echo "${DUMP_SIZE}B"), ${ELAPSED}s"

# --- Verify dump is not empty ---
if [ "$DUMP_SIZE" -lt 1024 ]; then
  log "ERROR: Dump file suspiciously small ($DUMP_SIZE bytes). Removing."
  rm -f "$TMP_FILE"
  exit 1
fi

# Verify we can decrypt it (round-trip check)
if ! openssl enc -aes-256-cbc -d -pbkdf2 -pass file:"$KEY_FILE" -in "$TMP_FILE" | gzip -t 2>/dev/null; then
  log "ERROR: Dump file failed decrypt+gzip integrity check. Removing."
  rm -f "$TMP_FILE"
  exit 1
fi

# Atomic rename — file only appears with final name after verification
mv "$TMP_FILE" "$DUMP_FILE"
log "Verified and saved: $DUMP_FILE"

# --- Promote Sunday dumps to weekly ---
if [ "$DAY_OF_WEEK" = "7" ]; then
  WEEKLY_FILE="$WEEKLY_DIR/ojs-weekly-$TIMESTAMP.sql.gz.enc"
  cp "$DUMP_FILE" "$WEEKLY_FILE"
  log "Sunday: promoted to weekly backup ($WEEKLY_FILE)"
fi

# --- Rotate old backups ---
rotate() {
  local dir="$1" keep="$2" label="$3"
  local count
  count=$(find "$dir" -name "ojs-*.sql.gz.enc" -type f 2>/dev/null | wc -l)
  if [ "$count" -gt "$keep" ]; then
    local to_delete=$(( count - keep ))
    find "$dir" -name "ojs-*.sql.gz.enc" -type f -printf '%T@ %p\n' \
      | sort -n | head -n "$to_delete" | awk '{print $2}' \
      | while read -r f; do
          log "Rotating $label: removing $(basename "$f")"
          rm -f "$f"
        done
  fi
}

rotate "$DAILY_DIR" "$KEEP_DAILY" "daily"
rotate "$WEEKLY_DIR" "$KEEP_WEEKLY" "weekly"

log "Backup complete. Daily: $(find "$DAILY_DIR" -name "ojs-*.sql.gz.enc" | wc -l)/$KEEP_DAILY, Weekly: $(find "$WEEKLY_DIR" -name "ojs-*.sql.gz.enc" | wc -l)/$KEEP_WEEKLY"
