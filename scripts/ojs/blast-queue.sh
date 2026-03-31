#!/bin/bash
# Drain the OJS job queue using `jobs.php run` in a loop.
#
# Usage:
#   scripts/ojs/blast-queue.sh                              # local dev, single worker (foreground)
#   scripts/ojs/blast-queue.sh --host=sea-live              # remote: nohup by default (survives SSH disconnect)
#   scripts/ojs/blast-queue.sh --host=sea-live --no-nohup   # remote: foreground (for debugging)
#   scripts/ojs/blast-queue.sh --workers=3                  # 3 parallel workers
#   scripts/ojs/blast-queue.sh --host=sea-live --purge      # clear queue without processing
#   scripts/ojs/blast-queue.sh --host=sea-live --kill       # kill any stale workers
#
# How it works:
#   Each worker loops: check queue size → exit if empty → run one batch → repeat.
#   `jobs.php run` processes up to 30 jobs per invocation then exits cleanly.
#   No daemon processes, no polling — workers exit reliably when the queue is empty.
#
#   Workers are written as a script file inside the container to avoid shell quoting
#   issues through SSH + docker exec layers. Each worker has a deadline (default 30 min)
#   after which it exits regardless.
#
#   With --workers=N, spawns N independent worker processes. Laravel's
#   FOR UPDATE SKIP LOCKED prevents race conditions. Capped at 5.
#
#   Crossref rate limit is ~2 requests/second. With ~2s per job, 3 workers
#   is the safe maximum for DOI deposits. Use --delay=1 for extra safety.
#
#   Stale workers from previous runs are killed before new ones start.

set -eo pipefail

WORKERS=1
HOST=""
NOHUP=""  # auto: true for remote, false for local
PURGE=false
KILL_ONLY=false
TRIES=1
DELAY=0  # seconds between jobs per worker (0 = no delay)
MAX_TIMEOUT=1800  # 30 minutes max runtime per worker
MAX_LOAD_WARN=2.0
MAX_LOAD_REFUSE=5.0
JOBS_PHP="/var/www/html/lib/pkp/tools/jobs.php"

for arg in "$@"; do
  case "$arg" in
    --host=*)    HOST="${arg#*=}" ;;
    --workers=*) WORKERS="${arg#*=}" ;;
    --no-nohup)  NOHUP=false ;;
    --purge)     PURGE=true ;;
    --kill)      KILL_ONLY=true ;;
    --tries=*)   TRIES="${arg#*=}" ;;
    --timeout=*) MAX_TIMEOUT="${arg#*=}" ;;
    --delay=*)   DELAY="${arg#*=}" ;;
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

# Guidance for Crossref deposits
if [ "$WORKERS" -gt 3 ]; then
  echo "NOTE: Crossref rate limit is ~2 req/s. For DOI deposits, 3 workers"
  echo "      with --delay=1 is recommended to avoid 429 errors."
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
  SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  source "$SCRIPTS_ROOT/lib/dc.sh"
  init_dc
  CONTAINER=$($DC ps --format '{{.Names}}' | grep ojs-1 | grep -v db)
  DOCKER_EXEC="docker exec $CONTAINER"
  LOAD_CMD="uptime"
fi

echo "Host: ${HOST:-local}"
echo "Container: $CONTAINER"

# --- Kill stale workers ---
# Find and kill any worker processes left over from previous runs.
# This prevents stale workers from processing jobs with old code.

kill_stale_workers() {
  local stale_pids
  if [ -n "$HOST" ]; then
    stale_pids=$($SSH "ps aux | grep -E 'jobs.php (work|run)|blast-worker' | grep -v grep | awk '{print \$2}'" 2>/dev/null) || true
  else
    stale_pids=$(ps aux | grep -E 'jobs.php (work|run)|blast-worker' | grep -v grep | awk '{print $2}') || true
  fi

  if [ -n "$stale_pids" ]; then
    local count
    count=$(echo "$stale_pids" | wc -l | tr -d ' ')
    echo "WARNING: Found $count stale worker(s) from a previous run."
    echo "$stale_pids" | while read -r pid; do
      echo "  Killing PID $pid"
      if [ -n "$HOST" ]; then
        $SSH "kill $pid" 2>/dev/null || true
      else
        kill "$pid" 2>/dev/null || true
      fi
    done
    sleep 1
    echo "  Stale workers killed."
  fi
}

# --- Kill-only mode ---

if $KILL_ONLY; then
  kill_stale_workers
  echo "Done."
  exit 0
fi

# --- Purge mode ---

if $PURGE; then
  echo ""
  TOTAL=$($DOCKER_EXEC php $JOBS_PHP total 2>&1 | grep -oP '\d+' || echo "0")
  echo "Purging all $TOTAL queued jobs..."
  $DOCKER_EXEC php $JOBS_PHP purge --all 2>&1
  echo "Done."
  exit 0
fi

# --- Kill stale workers before starting new ones ---

kill_stale_workers

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

DELAY_MSG=""
if [ "$DELAY" -gt 0 ] 2>/dev/null; then DELAY_MSG=", delay: ${DELAY}s"; fi
echo "Workers: $WORKERS (tries per job: $TRIES, timeout: ${MAX_TIMEOUT}s${DELAY_MSG})"
echo ""

# --- Write worker script into container ---
# Avoids all shell quoting issues with SSH + docker exec + bash -c.
# The script loops: check queue → exit if empty → process batch → repeat.
# Exits after MAX_TIMEOUT seconds regardless.

# Generate the worker script locally, then pipe it into the container.
# This avoids all quoting issues — the script never passes through bash -c.
WORKER_SCRIPT="#!/bin/bash
JOBS_PHP=$JOBS_PHP
DEADLINE=\$((SECONDS + $MAX_TIMEOUT))

while true; do
  if [ \$SECONDS -ge \$DEADLINE ]; then
    echo \"Timeout reached (${MAX_TIMEOUT}s)\"
    exit 0
  fi

  REMAINING=\$(php \$JOBS_PHP total 2>&1 | grep -oP '\d+' || echo '0')
  if [ \"\$REMAINING\" = '0' ]; then
    echo \"Queue empty\"
    exit 0
  fi

  php \$JOBS_PHP run --once 2>&1

  # Delay between jobs to avoid Crossref rate limiting
  if [ $DELAY -gt 0 ]; then
    sleep $DELAY
  fi
done"

if [ -n "$HOST" ]; then
  echo "$WORKER_SCRIPT" | $SSH "docker exec -i $CONTAINER bash -c 'cat > /tmp/blast-worker.sh && chmod +x /tmp/blast-worker.sh'"
else
  echo "$WORKER_SCRIPT" | docker exec -i "$CONTAINER" bash -c 'cat > /tmp/blast-worker.sh && chmod +x /tmp/blast-worker.sh'
fi

echo "Worker script written to container."

# --- Nohup mode (remote only) ---

if $NOHUP; then
  if [ -z "$HOST" ]; then
    echo "ERROR: --nohup only makes sense with --host="
    exit 1
  fi

  LOG="/tmp/blast-queue.log"
  echo "Starting $WORKERS worker(s) via nohup on $HOST..."
  echo "Log: $HOST:$LOG"
  echo "Max runtime: ${MAX_TIMEOUT}s per worker"
  echo ""

  if [ "$WORKERS" -eq 1 ]; then
    $SSH "nohup docker exec $CONTAINER bash /tmp/blast-worker.sh > $LOG 2>&1 &"
  else
    # Build inner command — only references the script file, no flags to mangle
    INNER=""
    for i in $(seq 1 "$WORKERS"); do
      INNER+="(bash /tmp/blast-worker.sh) & "
    done
    INNER+="wait"
    $SSH "nohup docker exec $CONTAINER bash -c '$INNER' > $LOG 2>&1 &"
  fi

  echo "Workers started in background on $HOST."
  echo "Monitor with: ssh $HOST tail -f $LOG"
  echo "Check queue:  ssh $HOST docker exec $CONTAINER php $JOBS_PHP total"
  echo "Kill workers: scripts/ojs/blast-queue.sh --host=$HOST --kill"
  exit 0
fi

# --- Foreground mode ---

# Progress monitor: show queue count, velocity, and ETA every 30 seconds
MONITOR_START=$(date +%s)
MONITOR_INITIAL=$TOTAL
monitor_progress() {
  while true; do
    sleep 30
    local remaining elapsed processed rate eta_s eta_m
    remaining=$($DOCKER_EXEC php $JOBS_PHP total 2>&1 | grep -oP '\d+' || echo "?")
    if [ "$remaining" != "?" ]; then
      elapsed=$(( $(date +%s) - MONITOR_START ))
      processed=$(( MONITOR_INITIAL - remaining ))
      if [ "$elapsed" -gt 0 ] && [ "$processed" -gt 0 ]; then
        rate=$(awk "BEGIN { printf \"%.1f\", $processed / ($elapsed / 60) }")
        eta_s=$(awk "BEGIN { printf \"%.0f\", $remaining / ($processed / $elapsed) }")
        eta_m=$(awk "BEGIN { printf \"%.1f\", $eta_s / 60 }")
        echo "  [queue: $remaining remaining | ${rate}/min | ETA: ${eta_m}m]"
      else
        echo "  [queue: $remaining remaining]"
      fi
    fi
  done
}

# Start progress monitor in background
monitor_progress &
MONITOR_PID=$!
trap 'kill $MONITOR_PID 2>/dev/null; wait $MONITOR_PID 2>/dev/null' EXIT

if [ "$WORKERS" -eq 1 ]; then
  echo "Starting single worker..."
  $DOCKER_EXEC bash /tmp/blast-worker.sh 2>&1
else
  echo "Starting $WORKERS workers..."
  PIDS=()

  for i in $(seq 1 "$WORKERS"); do
    $DOCKER_EXEC bash /tmp/blast-worker.sh 2>&1 | sed "s/^/[worker $i] /" &
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
