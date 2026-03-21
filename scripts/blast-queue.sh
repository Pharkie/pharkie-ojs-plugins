#!/bin/bash
# Drain the OJS job queue safely using `jobs.php work` (the proper worker daemon).
#
# Usage:
#   scripts/blast-queue.sh                             # local dev, single worker (foreground)
#   scripts/blast-queue.sh --host=sea-live             # remote: nohup by default (survives SSH disconnect)
#   scripts/blast-queue.sh --host=sea-live --no-nohup  # remote: foreground (for debugging)
#   scripts/blast-queue.sh --workers=2                 # 2 parallel workers (max 3)
#   scripts/blast-queue.sh --host=sea-live --purge     # clear queue without processing
#
# How it works:
#   Uses `jobs.php work --stop-when-empty` — a proper Laravel worker daemon that
#   processes jobs sequentially until the queue is empty, then exits. Much safer
#   than the old approach of spawning parallel `jobs.php run` in a loop.
#
#   With --workers=N, spawns N independent `jobs.php work` processes. Laravel's
#   FOR UPDATE SKIP LOCKED prevents race conditions. Max 5 to protect the VPS.

set -eo pipefail

WORKERS=1
HOST=""
NOHUP=""  # auto: true for remote, false for local
PURGE=false
TRIES=1
MAX_LOAD_WARN=2.0
MAX_LOAD_REFUSE=5.0
JOBS_PHP="/var/www/html/lib/pkp/tools/jobs.php"

for arg in "$@"; do
  case "$arg" in
    --host=*)    HOST="${arg#*=}" ;;
    --workers=*) WORKERS="${arg#*=}" ;;
    --no-nohup)  NOHUP=false ;;
    --purge)     PURGE=true ;;
    --tries=*)   TRIES="${arg#*=}" ;;
    --help|-h)
      sed -n '2,/^$/s/^# \?//p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg"
      echo "Run with --help for usage."
      exit 1
      ;;
  esac
done

# Default nohup: on for remote, off for local
if [ -z "$NOHUP" ]; then
  if [ -n "$HOST" ]; then NOHUP=true; else NOHUP=false; fi
fi

# Cap workers at 5
if [ "$WORKERS" -gt 5 ] 2>/dev/null; then
  echo "WARNING: Capping workers at 5 (requested $WORKERS)"
  WORKERS=5
fi

# --- Resolve container and commands ---

if [ -n "$HOST" ]; then
  SSH="ssh -o ConnectTimeout=10 $HOST"
  CONTAINER=$($SSH "docker ps --format '{{.Names}}' | grep ojs-1 | grep -v db" 2>/dev/null) || true
  if [ -z "$CONTAINER" ]; then
    echo "ERROR: OJS container not found on $HOST"
    exit 1
  fi
  DOCKER_EXEC="$SSH docker exec $CONTAINER"
  LOAD_CMD="$SSH uptime"
else
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  source "$SCRIPT_DIR/lib/dc.sh"
  init_dc
  CONTAINER=$($DC ps --format '{{.Names}}' | grep ojs-1 | grep -v db)
  DOCKER_EXEC="docker exec $CONTAINER"
  LOAD_CMD="uptime"
fi

echo "Host: ${HOST:-local}"
echo "Container: $CONTAINER"

# --- Purge mode ---

if $PURGE; then
  echo ""
  TOTAL=$($DOCKER_EXEC php $JOBS_PHP total 2>&1 | grep -oP '\d+' || echo "0")
  echo "Purging all $TOTAL queued jobs..."
  $DOCKER_EXEC php $JOBS_PHP purge --all 2>&1
  echo "Done."
  exit 0
fi

# --- Check queue size ---

TOTAL=$($DOCKER_EXEC php $JOBS_PHP total 2>&1 | grep -oP '\d+' || echo "0")
echo "Jobs in queue: $TOTAL"

if [ "$TOTAL" = "0" ]; then
  echo "Queue is empty, nothing to do."
  exit 0
fi

# --- Check server load ---

LOAD=$($LOAD_CMD 2>/dev/null | grep -oP 'load average: \K[\d.]+' || echo "0")
echo "Server load: $LOAD"

LOAD_STATUS=$(awk "BEGIN {
  if ($LOAD > $MAX_LOAD_REFUSE) print \"refuse\"
  else if ($LOAD > $MAX_LOAD_WARN) print \"warn\"
  else print \"ok\"
}")

if [ "$LOAD_STATUS" = "refuse" ]; then
  echo "ERROR: Server load ($LOAD) exceeds $MAX_LOAD_REFUSE — refusing to start."
  echo "       Wait for load to drop, or investigate what's consuming resources."
  exit 1
fi

if [ "$LOAD_STATUS" = "warn" ]; then
  echo "WARNING: Server load ($LOAD) is above $MAX_LOAD_WARN"
  echo "         Proceeding with $WORKERS worker(s), but watch the server."
fi

echo "Workers: $WORKERS (tries per job: $TRIES)"
echo ""

# --- Build the work command ---

WORK_CMD="php $JOBS_PHP work --stop-when-empty --tries=$TRIES"

# --- Nohup mode (remote only) ---

if $NOHUP; then
  if [ -z "$HOST" ]; then
    echo "ERROR: --nohup only makes sense with --host="
    exit 1
  fi

  LOG="/tmp/blast-queue.log"
  echo "Starting $WORKERS worker(s) via nohup on $HOST..."
  echo "Log: $HOST:$LOG"
  echo ""

  # Build the command to run inside the container
  if [ "$WORKERS" -eq 1 ]; then
    INNER_CMD="$WORK_CMD > /proc/1/fd/1 2>&1"
  else
    # Spawn N workers, wait for all
    INNER_CMD=""
    for i in $(seq 1 "$WORKERS"); do
      INNER_CMD+="($WORK_CMD) & "
    done
    INNER_CMD+="wait"
  fi

  # Run via nohup on the remote host, logging output
  $SSH "nohup docker exec $CONTAINER bash -c '$INNER_CMD' > $LOG 2>&1 &"

  echo "Workers started in background on $HOST."
  echo "Monitor with: ssh $HOST tail -f $LOG"
  echo "Check queue:  ssh $HOST docker exec $CONTAINER php $JOBS_PHP total"
  exit 0
fi

# --- Foreground mode ---

# Progress monitor: show queue count every 10 seconds
monitor_progress() {
  while true; do
    sleep 10
    local remaining
    remaining=$($DOCKER_EXEC php $JOBS_PHP total 2>&1 | grep -oP '\d+' || echo "?")
    echo "  [queue: $remaining remaining]"
  done
}

# Start progress monitor in background
monitor_progress &
MONITOR_PID=$!
trap 'kill $MONITOR_PID 2>/dev/null; wait $MONITOR_PID 2>/dev/null' EXIT

if [ "$WORKERS" -eq 1 ]; then
  echo "Starting single worker..."
  $DOCKER_EXEC php $JOBS_PHP work --stop-when-empty --tries="$TRIES" 2>&1
else
  echo "Starting $WORKERS workers..."
  PIDS=()

  for i in $(seq 1 "$WORKERS"); do
    $DOCKER_EXEC bash -c "$WORK_CMD" 2>&1 | sed "s/^/[worker $i] /" &
    PIDS+=($!)
  done

  # Wait for all workers
  for pid in "${PIDS[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
fi

# Kill progress monitor
kill $MONITOR_PID 2>/dev/null || true
wait $MONITOR_PID 2>/dev/null || true
trap - EXIT

echo ""
REMAINING=$($DOCKER_EXEC php $JOBS_PHP total 2>&1 | grep -oP '\d+' || echo "0")
echo "Done. Remaining in queue: $REMAINING"

# Show DOI status breakdown if remote
if [ -n "$HOST" ]; then
  DB_CONTAINER=$($SSH "docker ps --format '{{.Names}}' | grep ojs-db" 2>/dev/null) || true
  DB_PASS=$($DOCKER_EXEC grep -oP '(?<=password = ).*' /var/www/html/config.inc.php 2>/dev/null | head -1) || true
  if [ -n "$DB_CONTAINER" ] && [ -n "$DB_PASS" ]; then
    echo ""
    echo "DOI status:"
    $SSH "docker exec $DB_CONTAINER mariadb --skip-ssl -uojs -p'$DB_PASS' ojs -e 'SELECT status, COUNT(*) as n FROM dois GROUP BY status;'" 2>/dev/null || true
  fi
fi
