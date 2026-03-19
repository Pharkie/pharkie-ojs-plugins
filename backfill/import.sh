#!/bin/bash
# Import split issues into OJS.
#
# Takes the output of backfill/split-issue.sh (a directory containing import.xml)
# and loads it into OJS via the Native Import/Export CLI.
#
# Usage:
#   backfill/import.sh backfill/output/37.1       # Import one issue
#   backfill/import.sh backfill/output/*                  # Import all prepared issues
#
# Requires: OJS running in Docker (auto-detected), or --container=<name>.
#
# What it does:
#   1. Copies import.xml into the OJS container
#   2. Runs: php tools/importExport.php NativeImportExportPlugin import ...
#   3. Reports success/failure
#
# To split issues first, run: backfill/split-issue.sh <issue.pdf>
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Parse arguments ---
DIRS=()
CONTAINER=""
JOURNAL_PATH="ea"
ADMIN_USER="admin"
FORCE=0
CLEAN=0
for arg in "$@"; do
  case "$arg" in
    --container=*) CONTAINER="${arg#--container=}" ;;
    --journal=*) JOURNAL_PATH="${arg#--journal=}" ;;
    --admin=*) ADMIN_USER="${arg#--admin=}" ;;
    --force) FORCE=1 ;;
    --clean) CLEAN=1 ;;
    --help|-h)
      sed -n '2,/^set -eo/p' "$0" | head -n -1 | sed 's/^# \?//'
      exit 0
      ;;
    *) DIRS+=("$arg") ;;
  esac
done

# Validate JOURNAL_PATH and ADMIN_USER against allowed characters
if ! [[ "$JOURNAL_PATH" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "ERROR: Invalid --journal value '$JOURNAL_PATH' (only letters, digits, hyphens, underscores allowed)"
  exit 1
fi
if ! [[ "$ADMIN_USER" =~ ^[a-zA-Z0-9_-]+$ ]]; then
  echo "ERROR: Invalid --admin value '$ADMIN_USER' (only letters, digits, hyphens, underscores allowed)"
  exit 1
fi

if [ ${#DIRS[@]} -eq 0 ]; then
  echo "Usage: backfill/import.sh <issue-dir> [<issue-dir>...] [--container=<name>] [--force]"
  echo
  echo "Options:"
  echo "  --container=<name>  OJS Docker container (auto-detected if omitted)"
  echo "  --journal=<path>    Journal URL path (default: ea)"
  echo "  --admin=<user>      Admin username (default: admin)"
  echo "  --force             Reimport issues that already exist in OJS"
  echo "  --clean             Wipe all existing issues/articles before importing"
  echo
  echo "Example: backfill/import.sh backfill/output/37.1"
  echo "         backfill/import.sh backfill/output/*"
  exit 1
fi

# --- Find OJS container ---
if [ -z "$CONTAINER" ]; then
  CONTAINER=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '\-ojs-?1?$' | grep -v -E 'db|adminer' | head -1)
  if [ -z "$CONTAINER" ]; then
    echo "ERROR: No OJS Docker container found. Use --container=<name> or start OJS."
    exit 1
  fi
fi
echo "OJS container: $CONTAINER"

# --- Find OJS DB container ---
DB_CONTAINER=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -E '\-ojs-db-?1?$' | head -1)
if [ -z "$DB_CONTAINER" ]; then
  echo "WARNING: No OJS DB container found. Idempotency checks and ordering will be skipped."
fi

# Helper: run a MySQL query and return the result (requires DB_CONTAINER)
ojs_db_query() {
  local query="$1"
  docker exec "$DB_CONTAINER" mysql -N -u ojs -p"$OJS_DB_PASSWORD" ojs -e "$query" 2>/dev/null
}

# Read DB password from OJS container's environment
OJS_DB_PASSWORD=""
if [ -n "$DB_CONTAINER" ]; then
  OJS_DB_PASSWORD=$(docker exec "$CONTAINER" printenv OJS_DB_PASSWORD 2>/dev/null || true)
fi

echo

# --- Clean existing data if requested ---
if [ "$CLEAN" = "1" ] && [ -n "$DB_CONTAINER" ] && [ -n "$OJS_DB_PASSWORD" ]; then
  echo "--- Cleaning existing issues, articles, and sections ---"
  docker exec "$DB_CONTAINER" mysql -u ojs -p"$OJS_DB_PASSWORD" ojs -e "
    SET FOREIGN_KEY_CHECKS=0;
    DELETE FROM submission_file_settings;
    DELETE FROM submission_files;
    DELETE FROM publication_galley_settings;
    DELETE FROM publication_galleys;
    DELETE FROM publication_settings;
    DELETE FROM publications;
    DELETE FROM submission_settings;
    DELETE FROM submissions;
    DELETE FROM issue_galley_settings;
    DELETE FROM issue_galleys;
    DELETE FROM issue_settings;
    DELETE FROM issues;
    DELETE FROM section_settings;
    DELETE FROM sections WHERE journal_id = 1;
    SET FOREIGN_KEY_CHECKS=1;
  " 2>/dev/null
  echo "  OK: All existing issues and articles removed."
  echo
fi

FAILED=0
SUCCEEDED=0
SKIPPED=0

# Sort directories by volume.issue numerically (skip non-issue names like reports, Pro, etc.)
IFS=$'\n' SORTED_DIRS=($(for d in "${DIRS[@]}"; do
  base="$(basename "$d")"
  vol="${base%%.*}"
  # Skip entries where the volume part isn't numeric (e.g., "audit-report.json", "Pro")
  [[ "$vol" =~ ^[0-9]+$ ]] || continue
  if [[ "$base" == *.* ]]; then iss="${base#*.}"; else iss="0"; fi
  printf "%03d.%s\t%s\n" "$vol" "$iss" "$d"
done | sort -t. -k1,1n -k2,2n | cut -f2))
unset IFS

for DIR in "${SORTED_DIRS[@]}"; do
  DIR="$(cd "$DIR" 2>/dev/null && pwd)" || { echo "ERROR: $DIR not found"; FAILED=$((FAILED + 1)); continue; }
  XML_FILE="$DIR/import.xml"
  ISSUE_NAME="$(basename "$DIR")"

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Importing: $ISSUE_NAME"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if [ ! -f "$XML_FILE" ]; then
    echo "  SKIP: No import.xml in $DIR (not a prepared issue)"
    SKIPPED=$((SKIPPED + 1))
    echo
    continue
  fi

  XML_SIZE=$(du -h "$XML_FILE" | cut -f1)
  echo "  XML: $XML_FILE ($XML_SIZE)"

  # Extract volume and number from import.xml for idempotency check
  ISSUE_VOL=$(grep -oP '<volume>\K[^<]+' "$XML_FILE" | head -1)
  ISSUE_NUM=$(grep -oP '<number>\K[^<]+' "$XML_FILE" | head -1)

  # Validate vol/num are numeric (prevents shell injection into PHP)
  if [ -n "$ISSUE_VOL" ] && ! [[ "$ISSUE_VOL" =~ ^[0-9]+$ ]]; then
    echo "  WARNING: Non-numeric volume '$ISSUE_VOL' in XML, skipping idempotency check"
    ISSUE_VOL=""
  fi
  if [ -n "$ISSUE_NUM" ] && ! [[ "$ISSUE_NUM" =~ ^[0-9]+$ ]]; then
    echo "  WARNING: Non-numeric issue number '$ISSUE_NUM' in XML, skipping idempotency check"
    ISSUE_NUM=""
  fi

  if [ -n "$ISSUE_VOL" ] && [ -n "$ISSUE_NUM" ] && [ -n "$DB_CONTAINER" ]; then
    # Query OJS database to check if this issue already exists
    EXISTING=$(ojs_db_query "
      SELECT COUNT(*) FROM issues i
      JOIN journals j ON i.journal_id = j.journal_id
      WHERE j.path = '${JOURNAL_PATH}'
        AND i.volume = ${ISSUE_VOL}
        AND i.number = '${ISSUE_NUM}'
    " || echo "0")

    if [ "$EXISTING" -gt 0 ] 2>/dev/null; then
      if [ "$FORCE" -eq 0 ]; then
        echo "  SKIP: Vol ${ISSUE_VOL}.${ISSUE_NUM} already exists in OJS (use --force to reimport)"
        SKIPPED=$((SKIPPED + 1))
        echo
        continue
      else
        echo "  WARNING: Vol ${ISSUE_VOL}.${ISSUE_NUM} already exists, reimporting (--force)"
      fi
    fi
  fi

  # Copy XML into container
  docker cp "$XML_FILE" "$CONTAINER:/tmp/import.xml"

  # Run import
  echo "  Importing..."
  if docker exec "$CONTAINER" php tools/importExport.php \
    NativeImportExportPlugin import /tmp/import.xml "$JOURNAL_PATH" "$ADMIN_USER" 2>&1; then
    echo "  OK: $ISSUE_NAME imported"
    SUCCEEDED=$((SUCCEEDED + 1))
  else
    echo "  ERROR: Import failed for $ISSUE_NAME"
    FAILED=$((FAILED + 1))
  fi

  # Clean up
  docker exec "$CONTAINER" rm -f /tmp/import.xml

  echo
done

echo "=========================================="
echo "Complete: $SUCCEEDED imported, $SKIPPED skipped, $FAILED failed out of ${#SORTED_DIRS[@]}"
echo "=========================================="

# --- Fix archive ordering ---
# OJS archive page sorts by custom_issue_orders.seq first. Without entries,
# order is undefined. Populate with newest-first by date_published.
if [ $SUCCEEDED -gt 0 ] && [ -n "$DB_CONTAINER" ]; then
  echo ""
  echo "--- Fixing archive ordering ---"
  ORDERED=$(ojs_db_query "
    SET @jid = (SELECT journal_id FROM journals WHERE path = '${JOURNAL_PATH}' LIMIT 1);
    DELETE FROM custom_issue_orders WHERE journal_id = @jid;
    INSERT INTO custom_issue_orders (issue_id, journal_id, seq)
    SELECT issue_id, @jid, @seq := @seq + 1
    FROM issues, (SELECT @seq := 0) s
    WHERE journal_id = @jid
    ORDER BY date_published DESC;
    SELECT ROW_COUNT();
  ")
  echo "  OK: $ORDERED issues ordered (newest first by date_published)"

  # Set the newest issue as "current" (stored on journals.current_issue_id in OJS 3.5)
  ojs_db_query "
    SET @jid = (SELECT journal_id FROM journals WHERE path = '${JOURNAL_PATH}' LIMIT 1);
    SET @newest = (SELECT issue_id FROM issues WHERE journal_id = @jid ORDER BY date_published DESC LIMIT 1);
    UPDATE journals SET current_issue_id = @newest WHERE journal_id = @jid;
  " > /dev/null 2>&1
  CURRENT_ISSUE=$(ojs_db_query "
    SELECT CONCAT('Vol ', i.volume, '.', i.number) FROM issues i
    JOIN journals j ON j.current_issue_id = i.issue_id
    WHERE j.path = '${JOURNAL_PATH}' LIMIT 1;
  ")
  echo "  OK: $CURRENT_ISSUE set as current issue"
fi

[ $FAILED -eq 0 ] || exit 1
