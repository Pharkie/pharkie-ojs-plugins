#!/bin/bash
# Import split issues into OJS.
#
# Takes the output of backfill/split_pipeline/split_issue.sh (a directory containing import.xml)
# and loads it into OJS via the Native Import/Export CLI.
#
# Usage:
#   backfill/html_pipeline/pipe7_import.sh backfill/private/output/37.1       # Import one issue
#   backfill/html_pipeline/pipe7_import.sh backfill/private/output/*                  # Import all prepared issues
#
# Requires: OJS running in Docker (auto-detected), or --container=<name>.
#
# What it does:
#   1. Copies import.xml into the OJS container
#   2. Runs: php tools/importExport.php NativeImportExportPlugin import ...
#   3. Reports success/failure
#
# To split issues first, run: backfill/split_pipeline/split_issue.sh <issue.pdf>
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
    --wipe-articles) CLEAN=1 ;;
    --clean) CLEAN=1 ;;  # legacy alias
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

# If a single directory is passed that contains issue subdirectories, expand it
if [ ${#DIRS[@]} -eq 1 ] && [ -d "${DIRS[0]}" ]; then
  # Check if this looks like a parent dir (contains numbered subdirs) rather than an issue dir
  has_subdirs=0
  for sub in "${DIRS[0]}"/*/; do
    [ -d "$sub" ] && has_subdirs=1 && break
  done
  if [ "$has_subdirs" = "1" ] && [ ! -f "${DIRS[0]}/import.xml" ]; then
    DIRS=("${DIRS[0]}"/*/)
  fi
fi

if [ ${#DIRS[@]} -eq 0 ]; then
  echo "Usage: backfill/html_pipeline/pipe7_import.sh <issue-dir> [<issue-dir>...] [--container=<name>] [--force]"
  echo
  echo "Options:"
  echo "  --container=<name>  OJS Docker container (auto-detected if omitted)"
  echo "  --journal=<path>    Journal URL path (default: ea)"
  echo "  --admin=<user>      Admin username (default: admin)"
  echo "  --force             Reimport issues that already exist in OJS"
  echo "  --wipe-articles     Wipe all existing issues/articles before importing (users/subs/payments kept)"
  echo
  echo "Example: backfill/html_pipeline/pipe7_import.sh backfill/private/output/37.1"
  echo "         backfill/html_pipeline/pipe7_import.sh backfill/private/output/*"
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

# --- Suggest --wipe-articles for bulk imports ---
if [ ${#SORTED_DIRS[@]} -ge 5 ] && [ "$CLEAN" = "0" ]; then
  echo "WARNING: Importing ${#SORTED_DIRS[@]} issues without --wipe-articles."
  echo "  Existing issues will be skipped unless you also pass --force."
  echo "  For a full re-import, use: backfill/html_pipeline/pipe7_import.sh backfill/private/output/* --wipe-articles"
  echo
fi

# --- Clean existing data ---
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
  # NB: smarterSimilarArticles cache cleanup happens AFTER successful reimport
  # (further down) — not here. If the reimport crashes mid-way, readers
  # on live still get the previous day's sidebar (stale but not blank)
  # rather than blank until the next nightly rebuild.
fi

FAILED=0
SUCCEEDED=0
SKIPPED=0

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
        echo "  Deleting existing Vol ${ISSUE_VOL}.${ISSUE_NUM} before reimport..."
        # Get the issue_id and submission_ids for this specific issue
        ISSUE_ID=$(ojs_db_query "
          SELECT i.issue_id FROM issues i
          JOIN journals j ON i.journal_id = j.journal_id
          WHERE j.path = '${JOURNAL_PATH}'
            AND i.volume = ${ISSUE_VOL} AND i.number = '${ISSUE_NUM}' LIMIT 1;
        " || true)
        if [ -n "$ISSUE_ID" ]; then
          # Get submission IDs belonging to this issue
          SUB_IDS=$(ojs_db_query "
            SELECT DISTINCT submission_id FROM publications WHERE issue_id = ${ISSUE_ID};
          " | tr '\n' ',' | sed 's/,$//' || true)

          if [ -n "$SUB_IDS" ]; then
            ojs_db_query "
              SET FOREIGN_KEY_CHECKS=0;
              DELETE pgs FROM publication_galley_settings pgs
                JOIN publication_galleys pg ON pgs.galley_id = pg.galley_id
                JOIN publications p ON pg.publication_id = p.publication_id
                WHERE p.submission_id IN (${SUB_IDS});
              DELETE FROM publication_galleys WHERE publication_id IN
                (SELECT publication_id FROM publications WHERE submission_id IN (${SUB_IDS}));
              DELETE FROM submission_file_settings WHERE submission_file_id IN
                (SELECT submission_file_id FROM submission_files WHERE submission_id IN (${SUB_IDS}));
              DELETE FROM submission_files WHERE submission_id IN (${SUB_IDS});
              DELETE FROM publication_settings WHERE publication_id IN
                (SELECT publication_id FROM publications WHERE submission_id IN (${SUB_IDS}));
              DELETE FROM publications WHERE submission_id IN (${SUB_IDS});
              DELETE FROM submission_settings WHERE submission_id IN (${SUB_IDS});
              DELETE FROM submissions WHERE submission_id IN (${SUB_IDS});
              SET FOREIGN_KEY_CHECKS=1;
            " || true
          fi

          # Delete the issue itself
          ojs_db_query "
            SET FOREIGN_KEY_CHECKS=0;
            DELETE FROM issue_galley_settings WHERE issue_id = ${ISSUE_ID};
            DELETE FROM issue_galleys WHERE issue_id = ${ISSUE_ID};
            DELETE FROM issue_settings WHERE issue_id = ${ISSUE_ID};
            DELETE FROM custom_issue_orders WHERE issue_id = ${ISSUE_ID};
            DELETE FROM issues WHERE issue_id = ${ISSUE_ID};
            SET FOREIGN_KEY_CHECKS=1;
          " || true
          echo "  OK: Existing issue deleted."
        fi
      fi
    fi
  fi

  # Clear any stale jobs before import — OJS's shutdown handler drains the
  # queue at the end of the import PHP process, and stale jobs from prior
  # deletes/imports would log INVALID_PAYLOAD errors.
  if [ -n "$DB_CONTAINER" ] && [ -n "$OJS_DB_PASSWORD" ]; then
    ojs_db_query "DELETE FROM jobs;" > /dev/null 2>&1 || true
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

# --- Rebuild search index ---
# OJS Native Import doesn't trigger search indexing. rebuildSearchIndex.php
# only QUEUES UpdateSubmissionSearchIndexJob jobs into the `jobs` table —
# the actual indexing happens when jobs.php run processes them. Draining
# the queue is MANDATORY; do NOT DELETE FROM jobs afterwards — that
# regression (commit 82ba72d, 2026-03-31) is why live site search only
# returned results from the 2 most recent issues until 2026-04-16.
# See docs/ojs-issues-log.md.
if [ $SUCCEEDED -gt 0 ]; then
  echo ""
  echo "--- Rebuilding search index ---"
  if docker exec "$CONTAINER" php tools/rebuildSearchIndex.php 2>&1 | grep -v "CROSSREF BOOT"; then
    echo "  OK: Search index jobs scheduled"
    echo ""
    echo "--- Draining search index queue ---"
    echo "  This may take several minutes for large imports..."
    "$SCRIPT_DIR/../../scripts/ojs/blast-queue.sh" --workers=2 --timeout=3600
  else
    echo "  WARNING: Search index rebuild failed — run manually:"
    echo "    docker exec $CONTAINER php tools/rebuildSearchIndex.php"
    echo "    scripts/ojs/blast-queue.sh"
  fi
fi

# --- Clear smarterSimilarArticles cache after successful --wipe-articles reimport ---
# Done AFTER the import succeeds, not before: if reimport crashes mid-way,
# readers still get the previous (slightly stale) cache rather than blank
# sidebars until the next nightly rebuild fixes it.
# Table may not exist (installs without the smarterSimilarArticles plugin) — ignore
# "no such table" errors.
if [ "$CLEAN" = "1" ] && [ $SUCCEEDED -gt 0 ] && [ -n "$DB_CONTAINER" ] && [ -n "$OJS_DB_PASSWORD" ]; then
  docker exec "$DB_CONTAINER" mysql -u ojs -p"$OJS_DB_PASSWORD" ojs -e "TRUNCATE TABLE smarter_similar_articles" 2>/dev/null \
    && echo "  OK: smarterSimilarArticles cache cleared (awaiting next rebuild)" \
    || true
fi

# --- Reminder: restore IDs after wipe-articles import ---
if [ "$CLEAN" = "1" ] && [ $SUCCEEDED -gt 0 ]; then
  echo ""
  echo "NOTE: This was a --wipe-articles import. Restore IDs to preserve URLs/DOIs:"
  echo "  python backfill/html_pipeline/pipe8_restore.py --target dev"
  echo "(runs locally, reads JATS publisher-id, sends SQL to target via SSH)"
  echo "Also rebuild the smarterSimilarArticles cache:"
  echo "  python3 scripts/ojs/build_smarter_similar_articles.py --target=<host>"
fi

[ $FAILED -eq 0 ] || exit 1
