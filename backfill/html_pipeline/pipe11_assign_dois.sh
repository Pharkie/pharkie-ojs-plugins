#!/bin/bash
# Assign DOIs to a newly imported issue and its articles.
#
# Back-issues arrive with DOIs already registered at Crossref; a new issue has
# none, and the journal is set to doiCreationTime="never" so OJS will not mint
# them on its own. This copies pipe11_assign_dois.php into the OJS container and
# runs it there, using OJS's own repository code so suffixes match the archive.
#
# Idempotent: anything that already has a DOI is left alone.
#
# Usage:
#   backfill/html_pipeline/pipe11_assign_dois.sh 37.2 [--dry-run]
#   backfill/html_pipeline/pipe11_assign_dois.sh 37.2 --host=sea-live
#
# Assigning is not depositing — deposit from the OJS admin DOIs page afterwards.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
VOL_ISS=""
HOST=""
DRY_RUN=""

for arg in "$@"; do
  case "$arg" in
    --host=*) HOST="${arg#--host=}" ;;
    --dry-run) DRY_RUN="--dry-run" ;;
    *) VOL_ISS="$arg" ;;
  esac
done

if [ -z "$VOL_ISS" ]; then
  echo "Usage: $0 <vol>.<iss> [--host=sea-live] [--dry-run]" >&2
  exit 1
fi

VOL="${VOL_ISS%%.*}"
ISS="${VOL_ISS##*.}"
if [ "$VOL" = "$ISS" ]; then
  echo "ERROR: expected <vol>.<iss> (e.g. 37.2), got '$VOL_ISS'" >&2
  exit 1
fi

TOOL="$SCRIPT_DIR/pipe11_assign_dois.php"
REMOTE_TOOL="/var/www/html/backfill-assign-dois.php"

if [ -n "$HOST" ]; then
  source "$PROJECT_DIR/scripts/lib/resolve-ssh.sh"
  resolve_ssh "$HOST"
  REMOTE_DIR="/opt/pharkie-ojs-plugins"
  scp -q -i ~/.ssh/hetzner "$TOOL" "root@$SERVER_IP:/tmp/assign-dois.php"
  $SSH_CMD "cd $REMOTE_DIR \
    && docker compose cp /tmp/assign-dois.php ojs:$REMOTE_TOOL \
    && docker compose exec -T ojs php $REMOTE_TOOL $VOL $ISS $DRY_RUN; \
    rc=\$?; \
    docker compose exec -T ojs rm -f $REMOTE_TOOL; rm -f /tmp/assign-dois.php; exit \$rc"
else
  # Same container auto-detection as pipe7_import.sh -- works on the host, in
  # the devcontainer, and on the box, without needing a compose project dir.
  CONTAINER=$(docker ps --format '{{.Names}}' 2>/dev/null \
    | grep -E '\-ojs-?1?$' | grep -v -E 'db|adminer' | head -1)
  if [ -z "$CONTAINER" ]; then
    echo "ERROR: no running OJS container found" >&2
    exit 1
  fi
  echo "OJS container: $CONTAINER"
  docker cp "$TOOL" "$CONTAINER:$REMOTE_TOOL"
  set +e
  docker exec -i "$CONTAINER" php "$REMOTE_TOOL" "$VOL" "$ISS" $DRY_RUN
  rc=$?
  set -e
  docker exec -i "$CONTAINER" rm -f "$REMOTE_TOOL"
  exit $rc
fi
