#!/bin/bash
# Add whole-issue PDF galleys to existing OJS issues WITHOUT reimporting.
#
# This script adds issue galleys directly to the OJS database and file system,
# preserving all existing article IDs, DOIs, and URLs. Safe to run on live.
#
# Usage:
#   backfill/add-issue-galleys.sh [--host=sea-live] [--dry-run] [issue_dir...]
#
# Examples:
#   backfill/add-issue-galleys.sh backfill/private/output/*           # dev (local docker)
#   backfill/add-issue-galleys.sh --host=sea-live backfill/private/output/*  # live
#   backfill/add-issue-galleys.sh --dry-run backfill/private/output/*  # preview only

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
HOST=""
DRY_RUN=""
ISSUE_DIRS=()

for arg in "$@"; do
  case "$arg" in
    --host=*) HOST="${arg#--host=}" ;;
    --dry-run) DRY_RUN=1 ;;
    *) ISSUE_DIRS+=("$arg") ;;
  esac
done

if [ ${#ISSUE_DIRS[@]} -eq 0 ]; then
  echo "Usage: $0 [--host=sea-live] [--dry-run] <issue_dir>..."
  exit 1
fi

# Set up docker compose or SSH
if [ -n "$HOST" ]; then
  source "$PROJECT_DIR/scripts/lib/resolve-ssh.sh"
  resolve_ssh "$HOST"
  REMOTE_DIR="/opt/pharkie-ojs-plugins"
  # SSH mode: base64-encode SQL to avoid escaping hell, decode inside container.
  run_db() {
    local b64
    b64=$(echo "$1" | base64 -w0)
    $SSH_CMD "cd $REMOTE_DIR && docker compose exec -T ojs-db bash -c \"echo $b64 | base64 -d | mysql -u root -p\\\$MYSQL_ROOT_PASSWORD \\\$MYSQL_DATABASE -N\"" 2>/dev/null
  }
  run_ojs() {
    $SSH_CMD "cd $REMOTE_DIR && docker compose exec -T ojs bash -c '$1'" 2>/dev/null
  }
  copy_to_ojs() {
    scp -q -i ~/.ssh/hetzner "$1" "root@$SERVER_IP:/tmp/_galley_upload.pdf"
    $SSH_CMD "cd $REMOTE_DIR && docker compose cp /tmp/_galley_upload.pdf ojs:$2 && rm /tmp/_galley_upload.pdf" 2>/dev/null
  }
else
  source "$PROJECT_DIR/scripts/lib/dc.sh" 2>/dev/null && init_dc 2>/dev/null
  DC="${DC:-docker compose}"
  run_db() {
    echo "$1" | $DC exec -T ojs-db bash -c "mysql -u root -p\$MYSQL_ROOT_PASSWORD \$MYSQL_DATABASE -N" 2>/dev/null
  }
  run_ojs() {
    $DC exec -T ojs bash -c "$1" 2>/dev/null
  }
  copy_to_ojs() {
    $DC cp "$1" "ojs:$2" 2>/dev/null
  }
fi

# Get journal ID
JOURNAL_ID=$(run_db "SELECT journal_id FROM journals LIMIT 1")
if [ -z "$JOURNAL_ID" ]; then
  echo "ERROR: No journal found in OJS database."
  exit 1
fi

ADDED=0
SKIPPED=0
FAILED=0
MAX_FAILURES=3

# Sort issue dirs numerically (1, 2, 3, ..., 10.1, 10.2, ..., 37.1)
IFS=$'\n' SORTED_DIRS=($(for d in "${ISSUE_DIRS[@]}"; do echo "$d"; done | sort -t/ -k$(echo "${ISSUE_DIRS[0]}" | tr '/' '\n' | wc -l) -V))
unset IFS

for ISSUE_DIR in "${SORTED_DIRS[@]}"; do
  TOC="$ISSUE_DIR/toc.json"
  if [ ! -f "$TOC" ]; then
    echo "SKIP: No toc.json in $ISSUE_DIR"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Read metadata from toc.json
  read -r VOL ISS DATE < <(python3 -c "
import json, re, sys
from datetime import datetime
d = json.load(open('$TOC'))
vol = d['volume']
iss = d.get('issue', d.get('number', ''))
raw = d.get('date_published', d.get('date', ''))
if re.match(r'^\d{4}-\d{2}-\d{2}', raw):
    date = raw[:10]
else:
    try:
        date = datetime.strptime(raw, '%B %Y').strftime('%Y-%m-01')
    except:
        date = '2000-01-01'
print(f'{vol} {iss} {date}')
")

  VOL_ISS="${VOL}.${ISS}"
  if [ "$ISS" = "0" ] || [ -z "$ISS" ]; then
    VOL_ISS="$VOL"
  fi

  # Find source PDF
  SOURCE_PDF="$PROJECT_DIR/backfill/private/input/${VOL_ISS}.pdf"
  if [ ! -f "$SOURCE_PDF" ]; then
    SOURCE_PDF="$PROJECT_DIR/backfill/private/input/${VOL}.pdf"
  fi
  if [ ! -f "$SOURCE_PDF" ]; then
    echo "SKIP: No source PDF for $VOL_ISS"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Find issue ID in OJS
  ISSUE_ID=$(run_db "SELECT issue_id FROM issues WHERE volume='$VOL' AND number='$ISS' AND journal_id=$JOURNAL_ID LIMIT 1")
  if [ -z "$ISSUE_ID" ]; then
    echo "SKIP: Issue $VOL_ISS not found in OJS"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Check if issue already has a galley
  EXISTING=$(run_db "SELECT COUNT(*) FROM issue_galleys WHERE issue_id=$ISSUE_ID")
  if [ "$EXISTING" != "0" ]; then
    echo "SKIP: $VOL_ISS (id=$ISSUE_ID) already has a galley"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # Use pre-saved cleaned PDF if available, otherwise re-save from source
  PRESAVED="$ISSUE_DIR/issue-galley.pdf"
  CLEAN_PDF="/tmp/issue-galley-${VOL_ISS}.pdf"
  if [ -f "$PRESAVED" ]; then
    cp "$PRESAVED" "$CLEAN_PDF"
    CLEAN_SIZE=$(wc -c < "$CLEAN_PDF" | tr -d ' ')
    RESULT="${CLEAN_SIZE}b (pre-saved)"
  else
    RESULT=$(python3 -c "
import fitz, sys, os
try:
    doc = fitz.open('$SOURCE_PDF')
    pages = len(doc)
    clean = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    with open('$CLEAN_PDF', 'wb') as f:
        f.write(clean)
    orig = os.path.getsize('$SOURCE_PDF')
    print(f'{pages}p {orig:,} -> {len(clean):,}b')
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1)
    if [ $? -ne 0 ]; then
      echo "FAIL: $VOL_ISS — $RESULT"
      FAILED=$((FAILED + 1))
      continue
    fi
    CLEAN_SIZE=$(wc -c < "$CLEAN_PDF" | tr -d ' ')
  fi
  FILENAME="vol-${VOL}-iss-${ISS}.pdf"

  if [ -n "$DRY_RUN" ]; then
    echo "$VOL_ISS (id=$ISSUE_ID): $RESULT [dry-run]"
    rm -f "$CLEAN_PDF"
    ADDED=$((ADDED + 1))
    continue
  fi

  # Create directory, copy PDF, fix permissions
  FILES_DIR="/var/www/files/journals/$JOURNAL_ID/issues/$ISSUE_ID/public"
  run_ojs "mkdir -p $FILES_DIR && chown www-data:www-data $FILES_DIR"
  if ! copy_to_ojs "$CLEAN_PDF" "$FILES_DIR/$FILENAME"; then
    echo "FAIL: $VOL_ISS — copy failed"
    rm -f "$CLEAN_PDF"
    FAILED=$((FAILED + 1))
    if [ "$FAILED" -ge "$MAX_FAILURES" ]; then
      echo "ERROR: $MAX_FAILURES failures reached, aborting."
      break
    fi
    continue
  fi
  run_ojs "chown www-data:www-data $FILES_DIR/$FILENAME"

  # Insert DB rows (file first, then galley referencing it)
  run_db "INSERT INTO issue_files (issue_id, file_name, original_file_name, file_type, file_size, content_type, date_uploaded, date_modified) VALUES ($ISSUE_ID, '$FILENAME', '${VOL_ISS}.pdf', 'application/pdf', $CLEAN_SIZE, 1, '$DATE', '$DATE')"
  FILE_ID=$(run_db "SELECT file_id FROM issue_files WHERE issue_id=$ISSUE_ID AND file_name='$FILENAME' LIMIT 1")
  if [ -z "$FILE_ID" ]; then
    echo "FAIL: $VOL_ISS — issue_files insert failed"
    rm -f "$CLEAN_PDF"
    FAILED=$((FAILED + 1))
    if [ "$FAILED" -ge "$MAX_FAILURES" ]; then
      echo "ERROR: $MAX_FAILURES failures reached, aborting."
      break
    fi
    continue
  fi
  run_db "INSERT INTO issue_galleys (issue_id, file_id, locale, label, seq) VALUES ($ISSUE_ID, $FILE_ID, 'en', 'PDF', 0)"

  rm -f "$CLEAN_PDF"
  echo "$VOL_ISS (id=$ISSUE_ID): $RESULT — added"
  ADDED=$((ADDED + 1))
done

echo ""
echo "Done: $ADDED added, $SKIPPED skipped, $FAILED failed"
