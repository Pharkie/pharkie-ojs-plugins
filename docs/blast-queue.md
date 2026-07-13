# blast-queue.sh

Drains the OJS job queue (DOI deposits, notifications, etc.) by running `jobs.php run --once` in a loop inside the OJS container.

## Usage

```bash
# Local dev, single worker (foreground)
scripts/ojs/blast-queue.sh

# Local mode outside the devcontainer (e.g. directly on the server)
scripts/ojs/blast-queue.sh --env=prod

# Remote server, detached (survives SSH disconnect)
scripts/ojs/blast-queue.sh --host=<your-server>

# Remote, foreground (for debugging)
scripts/ojs/blast-queue.sh --host=<your-server> --no-nohup

# 3 parallel workers
scripts/ojs/blast-queue.sh --workers=3

# Purge the queue without processing
scripts/ojs/blast-queue.sh --host=<your-server> --purge

# Kill stale workers from a previous run
scripts/ojs/blast-queue.sh --host=<your-server> --kill

# Custom timeout (seconds) and retry count
scripts/ojs/blast-queue.sh --host=<your-server> --timeout=3600 --tries=1
```

### Flags

| Flag | Default | Description |
|---|---|---|
| `--host=NAME` | (local) | SSH host to run against. Omit for local dev. |
| `--env=ENV` | (auto) | Compose environment for local mode (`dev`/`staging`/`prod`), forwarded to `init_dc`. Required when running locally outside the devcontainer. |
| `--container=NAME` | (auto) | Target a known container directly, skipping compose resolution entirely. Used by `pipe7_import.sh`, which has already resolved the container. |
| `--workers=N` | 1 | Parallel workers (max 5). Laravel's `FOR UPDATE SKIP LOCKED` prevents double-processing. |
| `--no-nohup` | (auto) | Run in foreground on remote. Default for remote is nohup (background). |
| `--purge` | off | Delete all queued jobs without processing them. |
| `--kill` | off | Kill stale workers and exit. |
| `--tries=N` | 1 | Passed through to jobs.php (max attempts per job). |
| `--timeout=N` | 1800 | Max seconds per worker before it exits. |
| `--delay=N` | 0 | Seconds to sleep between jobs per worker. Use `--delay=1` for Crossref deposits to avoid 429s. |

## How it works

1. **Resolve the OJS container** -- finds the running `ojs-1` container, locally via `docker compose` or remotely via SSH.
2. **Kill stale workers** -- searches for leftover `jobs.php` or `blast-worker` processes and kills them before starting new ones.
3. **Check queue size** -- runs `jobs.php total`. Exits immediately if the queue is empty, and errors out (rather than assuming 0) if the count can't be read.
4. **Check server load** -- warns above 2.0, refuses to start above 5.0.
5. **Write a worker script into the container** -- pipes a bash script to `/tmp/blast-worker.sh` inside the container. This avoids shell quoting nightmares through SSH + docker exec + bash -c layers.
6. **Run the worker(s)** -- each worker loops: check queue size, exit if empty, run `jobs.php run --once` (processes one job), repeat. Workers also exit when `run` reports the default queue empty even though `total` is nonzero (see gotchas). Remote runs default to nohup so the process survives SSH disconnects.
7. **Report results** -- prints remaining queue size. On remote hosts, also queries the DB for DOI status breakdown.

### The worker loop (simplified)

```bash
DEADLINE=$((SECONDS + MAX_TIMEOUT))
while true; do
  [ $SECONDS -ge $DEADLINE ] && exit 0        # timeout
  REMAINING=$(php jobs.php total)
  [ "$REMAINING" = "0" ] && exit 0            # every queue drained
  OUT=$(php jobs.php run --once)              # process one job (default queue)
  case "$OUT" in
    *'No jobs available'*) exit 0 ;;          # default queue dry; rest is out of scope
  esac
done
```

A foreground progress monitor prints the remaining queue count every 30 seconds.

## Gotchas

### Don't use too many workers against production Crossref

Crossref's rate limit is ~2 requests/second. With 10 workers 429 errors were encountered on 942 out of 1,464 deposits. **3 workers with `--delay=1` is the recommended combination for DOI deposits** (~1 deposit/second, well under the limit). The script prints a warning if you use more than 3 workers. For non-Crossref jobs (e.g. notifications), more workers are fine.

### Stale workers process jobs with old code

If you ctrl-c or disconnect mid-run, worker processes can survive inside the container. On the next run they'll still be processing jobs using whatever PHP code was loaded when they started. The script kills stale workers on startup, but if you're debugging, run `--kill` explicitly first.

### `jobs.php work --stop-when-empty` is unreliable

OJS provides a `work` subcommand with `--stop-when-empty` that is supposed to exit when the queue drains. In practice it doesn't exit -- it keeps polling. That's why this script uses `jobs.php run --once` in a loop with an explicit queue-size check instead.

### `jobs.php total` counts every queue; `run` drains only the default one

`total` has no queue filter, but `run` only processes the configured default queue (or `--queue=`). Jobs sitting in another queue — e.g. `jobs.php test` dispatches into `queuedTestJob` — keep `total` nonzero while `run` has nothing to do. Workers therefore also exit when `run --once` prints its empty-queue message, reporting how many jobs remain in out-of-scope queues. See `docs/ojs-issues-log.md` #34.

### Runs from macOS too

The queue-count and load parsing is portable across GNU and BSD tools. (Before 2026-07 it used `grep -P`, which BSD grep lacks — run from a Mac, the script silently misread any queue as empty and exited, and the server-load guard was disabled the same way. The count check now errors out instead of guessing zero.)

### Timeout is between iterations, not mid-job

The deadline check happens at the top of the loop, before starting a new job. A job that begins before the deadline will run to completion. This means actual wall-clock time can exceed `--timeout` by the duration of one job.
