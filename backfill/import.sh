#!/bin/bash
# Import split issues into OJS.
#
# Takes the output of backfill/split-issue.sh (a directory containing import.xml)
# and loads it into OJS via the Native Import/Export CLI.
#
# Usage:
#   backfill/import.sh backfill/output/EA-vol37-iss1       # Import one issue
#   backfill/import.sh backfill/output/EA-vol*              # Import all prepared issues
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
JOURNAL_PATH="journal"
ADMIN_USER="admin"
for arg in "$@"; do
  case "$arg" in
    --container=*) CONTAINER="${arg#--container=}" ;;
    --journal=*) JOURNAL_PATH="${arg#--journal=}" ;;
    --admin=*) ADMIN_USER="${arg#--admin=}" ;;
    --help|-h)
      sed -n '2,/^set -eo/p' "$0" | head -n -1 | sed 's/^# \?//'
      exit 0
      ;;
    *) DIRS+=("$arg") ;;
  esac
done

if [ ${#DIRS[@]} -eq 0 ]; then
  echo "Usage: backfill/import.sh <issue-dir> [<issue-dir>...] [--container=<name>]"
  echo
  echo "Example: backfill/import.sh backfill/output/EA-vol37-iss1"
  echo "         backfill/import.sh backfill/output/EA-vol*"
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
echo

FAILED=0
SUCCEEDED=0

for DIR in "${DIRS[@]}"; do
  DIR="$(cd "$DIR" 2>/dev/null && pwd)" || { echo "ERROR: $DIR not found"; FAILED=$((FAILED + 1)); continue; }
  XML_FILE="$DIR/import.xml"
  ISSUE_NAME="$(basename "$DIR")"

  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "Importing: $ISSUE_NAME"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  if [ ! -f "$XML_FILE" ]; then
    echo "  ERROR: No import.xml in $DIR"
    echo "  Run backfill/prepare.sh first."
    FAILED=$((FAILED + 1))
    continue
  fi

  XML_SIZE=$(du -h "$XML_FILE" | cut -f1)
  echo "  XML: $XML_FILE ($XML_SIZE)"

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
echo "Complete: $SUCCEEDED imported, $FAILED failed out of ${#DIRS[@]}"
echo "=========================================="

[ $FAILED -eq 0 ] || exit 1
