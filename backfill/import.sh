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
for arg in "$@"; do
  case "$arg" in
    --container=*) CONTAINER="${arg#--container=}" ;;
    --journal=*) JOURNAL_PATH="${arg#--journal=}" ;;
    --admin=*) ADMIN_USER="${arg#--admin=}" ;;
    --force) FORCE=1 ;;
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
echo

FAILED=0
SUCCEEDED=0
SKIPPED=0

# Sort directories by volume.issue numerically
IFS=$'\n' SORTED_DIRS=($(for d in "${DIRS[@]}"; do
  base="$(basename "$d")"
  # Extract vol and issue for numeric sorting (e.g., "25.1" → "025.1", "3" → "003.0")
  vol="${base%%.*}"
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

  if [ -n "$ISSUE_VOL" ] && [ -n "$ISSUE_NUM" ]; then
    # Query OJS database to check if this issue already exists
    EXISTING=$(docker exec "$CONTAINER" php -r "
      require('tools/bootstrap.php');
      \$conn = \Illuminate\Database\Capsule\Manager::connection();
      \$count = \$conn->table('issues')
        ->join('journals', 'issues.journal_id', '=', 'journals.journal_id')
        ->where('journals.path', '${JOURNAL_PATH}')
        ->where('issues.volume', ${ISSUE_VOL})
        ->where('issues.number', '${ISSUE_NUM}')
        ->count();
      echo \$count;
    " 2>/dev/null || echo "0")

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

[ $FAILED -eq 0 ] || exit 1
