#!/bin/bash
# Deposit one issue's DOIs to Crossref.
#
# pipe11 assigns DOIs; this hands the metadata to the registration agency so
# they actually resolve. Scoped to a single issue on purpose — OJS's own
# "deposit all" would also sweep up unrelated stale DOIs.
#
# Dry-run by default: it lists what it would deposit. Pass --confirm to send.
#
# Usage:
#   backfill/html_pipeline/pipe12_deposit_dois.sh 37.2                    # list only
#   backfill/html_pipeline/pipe12_deposit_dois.sh 37.2 --host=sea-live --confirm
#
# Metadata correction on an already-registered DOI (Crossref holds a copy of
# authors/title, so it must be redeposited after e.g. an author-name fix):
#   backfill/html_pipeline/pipe12_deposit_dois.sh 37.2 --host=sea-live \
#     --redeposit=10.65828/rg2zhd62 --confirm
#
# Deposit is asynchronous: drain the job queue afterwards, and remember that
# "submitted" is not "registered" until Crossref confirms.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
VOL_ISS=""
HOST=""
CONFIRM=""

REDEPOSIT=""
for arg in "$@"; do
  case "$arg" in
    --host=*) HOST="${arg#--host=}" ;;
    --confirm) CONFIRM="--confirm" ;;
    --redeposit=*) REDEPOSIT="$arg" ;;
    *) VOL_ISS="$arg" ;;
  esac
done

if [ -z "$VOL_ISS" ]; then
  echo "Usage: $0 <vol>.<iss> [--host=sea-live] [--confirm]" >&2
  exit 1
fi

VOL="${VOL_ISS%%.*}"
ISS="${VOL_ISS##*.}"
if [ "$VOL" = "$ISS" ]; then
  echo "ERROR: expected <vol>.<iss> (e.g. 37.2), got '$VOL_ISS'" >&2
  exit 1
fi

TOOL="$SCRIPT_DIR/pipe12_deposit_dois.php"
REMOTE_TOOL="/var/www/html/backfill-deposit-dois.php"

if [ -n "$HOST" ]; then
  source "$PROJECT_DIR/scripts/lib/resolve-ssh.sh"
  resolve_ssh "$HOST"
  REMOTE_DIR="/opt/pharkie-ojs-plugins"
  scp -q -i ~/.ssh/hetzner "$TOOL" "root@$SERVER_IP:/tmp/deposit-dois.php"
  $SSH_CMD "cd $REMOTE_DIR \
    && docker compose cp /tmp/deposit-dois.php ojs:$REMOTE_TOOL \
    && docker compose exec -T ojs php $REMOTE_TOOL $VOL $ISS $CONFIRM $REDEPOSIT; \
    rc=\$?; \
    docker compose exec -T ojs rm -f $REMOTE_TOOL; sudo rm -f /tmp/deposit-dois.php 2>/dev/null || true; exit \$rc"
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
  docker exec -i "$CONTAINER" php "$REMOTE_TOOL" "$VOL" "$ISS" $CONFIRM $REDEPOSIT
  rc=$?
  set -e
  docker exec -i "$CONTAINER" rm -f "$REMOTE_TOOL"
  exit $rc
fi
