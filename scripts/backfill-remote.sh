#!/bin/bash
# Sync backfill import XMLs to a remote server and run the OJS import.
#
# Usage:
#   scripts/backfill-remote.sh                        # Sync + import on sea-staging
#   scripts/backfill-remote.sh --host=sea-staging     # Explicit host
#   scripts/backfill-remote.sh --sync-only            # Upload XMLs but don't import
#   scripts/backfill-remote.sh --import-only           # Import (XMLs already on server)
#   scripts/backfill-remote.sh --force                 # Reimport issues that already exist
#
# Prerequisites:
#   - hcloud CLI with active context
#   - backfill/output/*/import.xml files exist locally (run split-issue.sh first)
#   - OJS running on the remote server
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
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

source "$SCRIPT_DIR/lib/resolve-ssh.sh"
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
  BACKFILL_OUTPUT="$PROJECT_DIR/backfill/output"

  # Count import XMLs locally
  XML_COUNT=$(find "$BACKFILL_OUTPUT" -name 'import.xml' 2>/dev/null | wc -l)
  if [ "$XML_COUNT" -eq 0 ]; then
    echo "ERROR: No import.xml files found in backfill/output/"
    echo "Run split-issue.sh first."
    exit 1
  fi

  XML_SIZE=$(find "$BACKFILL_OUTPUT" -name 'import.xml' -exec du -ch {} + | tail -1 | cut -f1)
  echo "--- Packing $XML_COUNT import XMLs ($XML_SIZE) ---"

  # Create tar.gz of just the import.xml files (preserving dir structure)
  # XML with base64 compresses very well (~60-70% reduction)
  TARBALL="/tmp/backfill-import-xmls.tar.gz"
  (cd "$BACKFILL_OUTPUT" && find . -name 'import.xml' -print0 | tar czf "$TARBALL" --null -T -)
  TAR_SIZE=$(du -h "$TARBALL" | cut -f1)
  echo "  Compressed: $XML_SIZE → $TAR_SIZE"

  echo "--- Uploading to $SSH_HOST ---"
  $SCP_CMD "$TARBALL" "$SCP_HOST:/tmp/backfill-import-xmls.tar.gz"

  echo "--- Extracting on $SSH_HOST ---"
  $SSH_CMD "mkdir -p '$REMOTE_DIR/backfill/output' && \
    tar xzf /tmp/backfill-import-xmls.tar.gz -C '$REMOTE_DIR/backfill/output' && \
    rm /tmp/backfill-import-xmls.tar.gz"
  rm -f "$TARBALL"

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
  echo "Deploy first: scripts/deploy.sh --host=$SSH_HOST"
  exit 1
fi

# Full backfill always starts clean (wipes existing issues/articles) unless --force is used
# --force implies adding to existing data; without --force, --clean ensures a fresh start
CLEAN_FLAG=""
if [ -z "$FORCE" ]; then
  CLEAN_FLAG="--clean"
fi
$SSH_CMD "cd $REMOTE_DIR && bash backfill/import.sh backfill/output/* $FORCE $CLEAN_FLAG"

echo ""
echo "$(phase_time) === Backfill complete ==="
