#!/bin/bash
# Automated OJS database backup with encryption and rotation.
# Runs ON the VPS (called by cron or manually).
#
# Usage:
#   scripts/ojs/backup-ojs-db.sh              # Dump + compress + encrypt + rotate
#   scripts/ojs/backup-ojs-db.sh --dry-run    # Show what would happen
#
# Output: AES-256-CBC encrypted gzip files (.sql.gz.enc)
# Encryption key: /opt/backups/ojs/.backup-key (create once, back up separately)
#
# Retention: DB dumps 7 daily + 4 weekly; files tarball 3 daily + 4 weekly (Sunday dumps promoted).
# Backups stored in /opt/backups/ojs/
#
# Cron (installed by --install-cron from pull-ojs-backup.sh):
#   0 3 * * * /opt/pharkie-ojs-plugins/scripts/ojs/backup-ojs-db.sh >> /opt/backups/ojs/backup.log 2>&1
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
KEEP_DAILY_FILES=3
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
if [ -z "$WP_DB_PASSWORD" ]; then
  log "WARNING: WP_DB_PASSWORD not set — skipping WordPress backup"
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

# --- WordPress database backup (same pattern) ---
if [ -n "$WP_DB_PASSWORD" ]; then
  WP_DUMP_FILE="$DAILY_DIR/wp-$TIMESTAMP.sql.gz.enc"
  WP_TMP_FILE="${WP_DUMP_FILE}.tmp"

  if docker compose -f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.staging.yml" \
    ps --status running 2>/dev/null | grep -q wp-db; then
    log "Starting WordPress database backup..."
    WP_START=$(date +%s)
    docker compose -f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.staging.yml" \
      exec -T wp-db mariadb-dump \
      --single-transaction \
      --routines \
      --triggers \
      -u "${WP_DB_USER:-wordpress}" -p"$WP_DB_PASSWORD" "${WP_DB_NAME:-wordpress}" \
      | gzip \
      | openssl enc -aes-256-cbc -pbkdf2 -pass file:"$KEY_FILE" -out "$WP_TMP_FILE"

    WP_DUMP_SIZE=$(stat -c%s "$WP_TMP_FILE" 2>/dev/null || stat -f%z "$WP_TMP_FILE" 2>/dev/null)
    WP_ELAPSED=$(( $(date +%s) - WP_START ))
    log "WP dump complete: $(numfmt --to=iec "$WP_DUMP_SIZE" 2>/dev/null || echo "${WP_DUMP_SIZE}B"), ${WP_ELAPSED}s"

    if [ "$WP_DUMP_SIZE" -lt 1024 ]; then
      log "WARNING: WP dump suspiciously small ($WP_DUMP_SIZE bytes). Removing."
      rm -f "$WP_TMP_FILE"
    elif ! openssl enc -aes-256-cbc -d -pbkdf2 -pass file:"$KEY_FILE" -in "$WP_TMP_FILE" | gzip -t 2>/dev/null; then
      log "WARNING: WP dump failed integrity check. Removing."
      rm -f "$WP_TMP_FILE"
    else
      mv "$WP_TMP_FILE" "$WP_DUMP_FILE"
      log "WP backup saved: $WP_DUMP_FILE"
    fi
  else
    log "WARNING: wp-db container not running — skipping WP backup"
  fi
fi

# --- OJS files volume backup (PDFs, HTML galleys) ---
OJS_FILES_DUMP="$DAILY_DIR/ojs-files-$TIMESTAMP.tar.gz.enc"
OJS_FILES_TMP="${OJS_FILES_DUMP}.tmp"
if docker compose -f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.staging.yml" \
  ps --status running 2>/dev/null | grep -q 'ojs-1\|ojs_1'; then
  log "Starting OJS files volume backup..."
  FILES_START=$(date +%s)
  docker compose -f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.staging.yml" \
    exec -T ojs tar czf - -C /var/www/files . \
    | openssl enc -aes-256-cbc -pbkdf2 -pass file:"$KEY_FILE" -out "$OJS_FILES_TMP"

  FILES_SIZE=$(stat -c%s "$OJS_FILES_TMP" 2>/dev/null || stat -f%z "$OJS_FILES_TMP" 2>/dev/null)
  FILES_ELAPSED=$(( $(date +%s) - FILES_START ))
  log "Files backup complete: $(numfmt --to=iec "$FILES_SIZE" 2>/dev/null || echo "${FILES_SIZE}B"), ${FILES_ELAPSED}s"

  if [ "$FILES_SIZE" -lt 100 ]; then
    log "WARNING: Files backup suspiciously small ($FILES_SIZE bytes). Removing."
    rm -f "$OJS_FILES_TMP"
  else
    mv "$OJS_FILES_TMP" "$OJS_FILES_DUMP"
    log "Files backup saved: $OJS_FILES_DUMP"
  fi
else
  log "WARNING: OJS container not running — skipping files backup"
fi

# --- Promote Sunday dumps to weekly ---
if [ "$DAY_OF_WEEK" = "7" ]; then
  WEEKLY_FILE="$WEEKLY_DIR/ojs-weekly-$TIMESTAMP.sql.gz.enc"
  cp "$DUMP_FILE" "$WEEKLY_FILE"
  log "Sunday: promoted OJS DB to weekly ($WEEKLY_FILE)"
  if [ -f "$WP_DUMP_FILE" ]; then
    cp "$WP_DUMP_FILE" "$WEEKLY_DIR/wp-weekly-$TIMESTAMP.sql.gz.enc"
    log "Sunday: promoted WP DB to weekly"
  fi
  if [ -f "$OJS_FILES_DUMP" ]; then
    cp "$OJS_FILES_DUMP" "$WEEKLY_DIR/ojs-files-weekly-$TIMESTAMP.tar.gz.enc"
    log "Sunday: promoted OJS files to weekly"
  fi
fi

# --- Rotate old backups ---
rotate() {
  local dir="$1" pattern="$2" keep="$3" label="$4"
  local count
  count=$(find "$dir" -name "$pattern" -type f 2>/dev/null | wc -l)
  if [ "$count" -gt "$keep" ]; then
    local to_delete=$(( count - keep ))
    find "$dir" -name "$pattern" -type f -printf '%T@ %p\n' \
      | sort -n | head -n "$to_delete" | awk '{print $2}' \
      | while read -r f; do
          log "Rotating $label: removing $(basename "$f")"
          rm -f "$f"
        done
  fi
}

rotate "$DAILY_DIR" "ojs-2*.sql.gz.enc" "$KEEP_DAILY" "daily OJS DB"
rotate "$DAILY_DIR" "wp-*.sql.gz.enc" "$KEEP_DAILY" "daily WP DB"
rotate "$DAILY_DIR" "ojs-files-*.tar.gz.enc" "$KEEP_DAILY_FILES" "daily OJS files"
rotate "$WEEKLY_DIR" "ojs-weekly-*.sql.gz.enc" "$KEEP_WEEKLY" "weekly OJS DB"
rotate "$WEEKLY_DIR" "wp-weekly-*.sql.gz.enc" "$KEEP_WEEKLY" "weekly WP DB"
rotate "$WEEKLY_DIR" "ojs-files-weekly-*.tar.gz.enc" "$KEEP_WEEKLY" "weekly OJS files"

log "Backup complete."
