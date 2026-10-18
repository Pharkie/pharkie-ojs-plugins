#!/bin/bash
# Install the nightly backup line in root's crontab, with its heartbeat ping.
# Runs ON the VPS. Called by scripts/infra/deploy.sh (as the deploy user, which
# may `sudo crontab`) and by scripts/infra/pull-ojs-backup.sh --install-cron (as
# root). Safe to run again: a crontab that already has the line is left alone.
#
# The line it writes:
#   0 3 * * * <project>/scripts/ojs/backup-ojs-db.sh >> /opt/backups/ojs/backup.log 2>&1 && curl -sf <hb> > /dev/null || curl -sf <hb>/fail > /dev/null
#
# <hb> is BETTERSTACK_HB_OJS_BACKUP from the project's .env. backup-ojs-db.sh
# exits non-zero on a failed dump, a failed off-site copy or a refused expiry,
# and that exit status is the whole alarm: nothing else reads the log. So if the
# variable is unset, the line goes in without the ping and this prints a
# WARNING, because the heartbeat will go down and stay down with a clean log.
#
# PROJECT_DIR defaults to /opt/pharkie-ojs-plugins; it is overridable for tests.
set -eo pipefail

PROJECT_DIR="${PROJECT_DIR:-/opt/pharkie-ojs-plugins}"
BACKUP_DIR="/opt/backups/ojs"
ENV_FILE="$PROJECT_DIR/.env"
BACKUP_SCRIPT="$PROJECT_DIR/scripts/ojs/backup-ojs-db.sh"
CURRENT_PATH="scripts/ojs/backup-ojs-db.sh"
LEGACY_PATH="scripts/backup-ojs-db.sh"

# Root's crontab: directly as root, through sudo as the deploy user.
SUDO=""
[ "$(id -u)" -eq 0 ] || SUDO="sudo -n"

# Heartbeat tokens never reach a terminal or a log.
mask() { sed -E 's#(/heartbeat/)[^/[:space:]]+#\1<token>#g'; }

# "No crontab yet" is an empty one. Any other failure to read it stops here:
# writing back after a failed read would wipe root's other lines.
read_crontab() {
  local out err rc=0
  err=$(mktemp)
  out=$($SUDO crontab -l 2>"$err") || rc=$?
  if [ "$rc" -ne 0 ] && ! grep -qi "no crontab" "$err"; then
    echo "ERROR: could not read root's crontab: $(cat "$err")" >&2
    rm -f "$err"
    return 1
  fi
  rm -f "$err"
  printf '%s' "$out"
}

CRONTAB_CURRENT=$(read_crontab)

# The script moved from scripts/ to scripts/ojs/; carry an old line across.
if printf '%s\n' "$CRONTAB_CURRENT" | grep -qF "$LEGACY_PATH" \
   && ! printf '%s\n' "$CRONTAB_CURRENT" | grep -qF "$CURRENT_PATH"; then
  printf '%s\n' "$CRONTAB_CURRENT" | sed "s|$LEGACY_PATH|$CURRENT_PATH|g" | $SUDO crontab -
  echo "[ok] Backup cron migrated to new path."
  CRONTAB_CURRENT=$(read_crontab)
fi

if printf '%s\n' "$CRONTAB_CURRENT" | grep -qF "$CURRENT_PATH"; then
  INSTALLED=$(printf '%s\n' "$CRONTAB_CURRENT" | grep -F "$CURRENT_PATH")
  echo "[ok] Backup cron already installed:"
  printf '%s\n' "$INSTALLED" | mask | sed 's/^/  /'
  if ! printf '%s\n' "$INSTALLED" | grep -qF "curl"; then
    echo "WARNING: that line pings no heartbeat, so the backup heartbeat will go down."
    echo "         Set BETTERSTACK_HB_OJS_BACKUP in $ENV_FILE, remove the line"
    echo "         (scripts/infra/pull-ojs-backup.sh --remove-cron), and install it again."
  fi
  exit 0
fi

if [ ! -f "$BACKUP_SCRIPT" ]; then
  echo "[skip] $BACKUP_SCRIPT not found, skipping cron."
  exit 0
fi

# The value as backup-ojs-db.sh will see it: the .env sourced, not grepped.
HB=""
if [ -r "$ENV_FILE" ]; then
  # shellcheck source=/dev/null
  HB=$(unset BETTERSTACK_HB_OJS_BACKUP; set -a; . "$ENV_FILE" >/dev/null 2>&1; printf '%s' "${BETTERSTACK_HB_OJS_BACKUP:-}")
fi
HB="${HB%/}"

CRON_LINE="0 3 * * * $BACKUP_SCRIPT >> $BACKUP_DIR/backup.log 2>&1"
# cron reads % as a newline, and a space would split the URL from curl.
URL_RE='^https://[^[:space:]%]+$'
if [ -z "$HB" ]; then
  echo "WARNING: BETTERSTACK_HB_OJS_BACKUP is not set in $ENV_FILE (or the file is unreadable)."
  echo "         Installing the backup line WITHOUT its heartbeat ping: the backup"
  echo "         heartbeat will go down and stay down while the log looks clean."
elif ! [[ "$HB" =~ $URL_RE ]]; then
  echo "WARNING: BETTERSTACK_HB_OJS_BACKUP in $ENV_FILE is not a plain https:// URL"
  echo "         (no spaces, no %). Installing the backup line WITHOUT its heartbeat ping."
else
  CRON_LINE="$CRON_LINE && curl -sf $HB > /dev/null || curl -sf $HB/fail > /dev/null"
fi

if [ ! -d "$BACKUP_DIR/daily" ] || [ ! -d "$BACKUP_DIR/weekly" ]; then
  $SUDO mkdir -p "$BACKUP_DIR/daily" "$BACKUP_DIR/weekly" \
    || echo "WARNING: could not create $BACKUP_DIR/{daily,weekly}; the log's directory must exist before 03:00."
fi

{
  if [ -n "$CRONTAB_CURRENT" ]; then printf '%s\n' "$CRONTAB_CURRENT"; fi
  printf '%s\n' "$CRON_LINE"
} | $SUDO crontab -
echo "[ok] Backup cron installed (daily 03:00 UTC):"
printf '%s\n' "$CRON_LINE" | mask | sed 's/^/  /'
