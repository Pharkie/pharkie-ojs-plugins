#!/bin/bash
# Sync backfill import XMLs to a remote server and run the OJS import.
#
# Usage:
#   scripts/dev/backfill-remote.sh                        # Sync + import on sea-staging
#   scripts/dev/backfill-remote.sh --host=sea-staging     # Explicit host
#   scripts/dev/backfill-remote.sh --sync-only            # Upload XMLs but don't import
#   scripts/dev/backfill-remote.sh --import-only           # Import (XMLs already on server)
#   scripts/dev/backfill-remote.sh --force                 # Reimport issues that already exist
#
# Prerequisites:
#   - hcloud CLI with active context
#   - backfill/private/output/*/import.xml files exist locally (run split-issue.sh first)
#   - OJS running on the remote server
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(dirname "$SCRIPTS_ROOT")"
REMOTE_DIR="/opt/pharkie-ojs-plugins"

# --- Parse arguments ---
SSH_HOST="sea-staging"
SYNC_ONLY=""
IMPORT_ONLY=""
FORCE=""
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
    --sync-only) SYNC_ONLY=1 ;;
    --import-only) IMPORT_ONLY=1 ;;
    --force) FORCE="--force" ;;
    --help|-h)
      sed -n '2,/^set -eo/p' "$0" | head -n -1 | sed 's/^# \?//'
      exit 0
      ;;
  esac
done

source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

START=$(date +%s)
phase_time() {
  local now=$(date +%s)
  local elapsed=$(( now - START ))
  printf "[%dm%02ds]" $((elapsed / 60)) $((elapsed % 60))
}

echo "=== Backfill remote: $SSH_HOST ==="

# --- Sync import XMLs ---
if [ -z "$IMPORT_ONLY" ]; then
  BACKFILL_OUTPUT="$PROJECT_DIR/backfill/private/output"

  # Count import XMLs locally
  XML_COUNT=$(find "$BACKFILL_OUTPUT" -name 'import.xml' 2>/dev/null | wc -l)
  if [ "$XML_COUNT" -eq 0 ]; then
    echo "ERROR: No import.xml files found in backfill/private/output/"
    echo "Run split-issue.sh first."
    exit 1
  fi

  XML_SIZE=$(find "$BACKFILL_OUTPUT" -name 'import.xml' -exec du -ch {} + | tail -1 | cut -f1)
  echo "--- Packing $XML_COUNT import XMLs ($XML_SIZE) ---"

  # Create tar.gz of just the import.xml files (preserving dir structure)
  # XML with base64 compresses very well (~60-70% reduction)
  TARBALL="/tmp/backfill-import-xmls.tar.gz"
  # COPYFILE_DISABLE stops macOS bsdtar adding an AppleDouble "._import.xml"
  # entry per file to carry xattrs. GNU tar on the box then fails to write them
  # ("._import.xml: Cannot open: Permission denied") and aborts the extraction
  # with "Exiting with failure status", leaving the real import.xml files as
  # they were. The sync LOOKS like it ran and the box keeps the old XML, so the
  # reimport that follows silently republishes stale content. Cost one
  # maintenance window on 2026-08-03. Harmless on Linux, where it is ignored.
  (cd "$BACKFILL_OUTPUT" && find . -name 'import.xml' -print0 \
     | COPYFILE_DISABLE=1 tar czf "$TARBALL" --null -T -)
  TAR_SIZE=$(du -h "$TARBALL" | cut -f1)
  echo "  Compressed: $XML_SIZE → $TAR_SIZE"

  echo "--- Uploading to $SSH_HOST ---"
  $SCP_CMD "$TARBALL" "$SCP_HOST:/tmp/backfill-import-xmls.tar.gz"

  echo "--- Extracting on $SSH_HOST ---"
  # --overwrite, explicitly. Without it GNU tar refused to replace one existing
  # file — "./37.2/import.xml: Cannot open: File exists" — and, because the
  # extraction is chained with &&, aborted before anything was verified. The box
  # kept a week-old XML while the script reported success. Not TAR_OPTIONS, not
  # permissions, not disk: tar simply would not clobber that file, and
  # --overwrite makes the intent explicit rather than relying on the default.
  $SSH_CMD "mkdir -p '$REMOTE_DIR/backfill/private/output' && \
    tar xzf /tmp/backfill-import-xmls.tar.gz --overwrite -C '$REMOTE_DIR/backfill/private/output' && \
    rm /tmp/backfill-import-xmls.tar.gz"
  rm -f "$TARBALL"

  # --- Prove it actually landed ---
  #
  # tar's exit status is not enough, and neither is "no errors scrolled past".
  # On 2026-08-03 this sync reported success while leaving 37.2/import.xml on the
  # box untouched — dated a week earlier, a different size — and the reimport
  # that followed would have quietly republished the uncorrected article. That is
  # the worst failure this script can have: it undoes a correction and says OK.
  #
  # So compare sizes, every file, every run. Cheap (one ssh, one stat per file)
  # against the cost of finding out later from a reader.
  echo "--- Verifying ---"
  # LC_ALL=C sort on BOTH sides, applied after collection. macOS and Linux sort
  # differently by default (locale collation), so sorting inside each `find`
  # produced two orderings of the same data and the diff flagged every line
  # while the single real mismatch was buried. Compare sets, not sequences.
  LOCAL_SIZES=$(cd "$BACKFILL_OUTPUT" && find . -name 'import.xml' \
    | while read -r f; do echo "${f#./} $(wc -c < "$f" | tr -d ' ')"; done \
    | LC_ALL=C sort)
  REMOTE_SIZES=$($SSH_CMD "cd '$REMOTE_DIR/backfill/private/output' && \
    find . -name import.xml | while read -r f; do \
      echo \"\${f#./} \$(wc -c < \"\$f\" | tr -d ' ')\"; done" | LC_ALL=C sort)

  if [ "$LOCAL_SIZES" = "$REMOTE_SIZES" ]; then
    echo "  OK: $(echo "$LOCAL_SIZES" | wc -l | tr -d ' ') import XMLs match local byte-for-byte"
  else
    echo "  MISMATCH — these did not sync correctly:"
    diff <(echo "$LOCAL_SIZES") <(echo "$REMOTE_SIZES") | grep '^[<>]' | head -20
    echo ""
    echo "  DO NOT IMPORT. The box holds different bytes from local, so a reimport"
    echo "  would publish the wrong content. Re-run the sync, or scp the offending"
    echo "  files directly, then check again."
    exit 1
  fi

  echo "$(phase_time) Sync complete."

  if [ -n "$SYNC_ONLY" ]; then
    echo "=== Sync-only mode, skipping import ==="
    exit 0
  fi
fi

# --- Run import on remote ---
echo "--- Running import on $SSH_HOST ---"

# Verify OJS container is running
if ! $SSH_CMD "docker ps --format '{{.Names}}' | grep -qE '\-ojs-?1?\$'"; then
  echo "ERROR: No OJS container running on $SSH_HOST"
  echo "Deploy first: scripts/infra/deploy.sh --host=$SSH_HOST"
  exit 1
fi

# Full backfill wipes existing issues/articles (not users/subs/payments) unless --force is used
# --force implies adding to existing data; without --force, --wipe-articles ensures a fresh start
CLEAN_FLAG=""
if [ -z "$FORCE" ]; then
  CLEAN_FLAG="--wipe-articles"
fi
$SSH_CMD "cd $REMOTE_DIR && bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/* $FORCE $CLEAN_FLAG"

echo ""
echo "$(phase_time) === Backfill complete ==="
