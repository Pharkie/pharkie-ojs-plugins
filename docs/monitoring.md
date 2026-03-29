# Monitoring

## Overview

Monitoring uses [Better Stack](https://uptime.betterstack.com) with two types of checks:

- **URL monitors** — Better Stack pings public URLs directly and alerts if they go down
- **Heartbeat monitors** — periodic jobs ping Better Stack on completion; alert fires if a ping is missed

Public status page: https://status.existentialanalysis.org.uk/

## URL monitors (8)

Created by `scripts/setup-betterstack.sh`. These check that public-facing services respond:

| Monitor | URL | Type |
|---|---|---|
| OJS Homepage | journal.existentialanalysis.org.uk | status |
| OJS Journal | journal.existentialanalysis.org.uk/index.php/ea | status |
| OJS Article Content | (same, keyword check) | keyword |
| OJS Login Page | journal.existentialanalysis.org.uk/index.php/ea/login | keyword |
| WP Homepage | community.existentialanalysis.org.uk | status |
| WP REST API | community.existentialanalysis.org.uk/wp-json/ | status |
| WP Admin | community.existentialanalysis.org.uk/wp-admin/ | status |
| OJS index redirect | journal.existentialanalysis.org.uk/ea/index | status |

## Heartbeat monitors (5)

| Heartbeat | Period | Triggered by | Notes |
|---|---|---|---|
| SEA: Hourly monitoring | 1h + 30m grace | GitHub Actions `monitor-hourly.yml` | Runs `scripts/monitor-safe.sh --host=sea-live` |
| SEA: Daily monitoring | 1d + 2h grace | GitHub Actions `monitor-daily.yml` | Runs `scripts/monitor-deep.sh --host=sea-live` + Playwright |
| SEA: Database backup | 1d + 2h grace | Server crontab (03:00 UTC) | Pinged by `scripts/backup-ojs-db.sh` |
| SEA: OJS scheduled tasks | 1h + 30m grace | OJS container cron | Pinged by `ojs-scheduler-heartbeat.sh` inside the OJS container |
| SEA: GitHub backup pull | 1d + 2h grace | GitHub Actions `backup.yml` | Not yet active (pending) |

## Monitoring scripts

All scripts run **remotely via SSH** from GitHub Actions (or the devcontainer). They do not run on the server itself.

- **`scripts/monitor-safe.sh --host=sea-live`** — non-mutating checks: HTTP responses, plugin status, subscription config, Stripe, job queue, server resources, container health, WP health. Pings `BETTERSTACK_HB_HOURLY`.
- **`scripts/monitor-deep.sh --host=sea-live`** — runs all safe checks PLUS: sync round-trip, backup health, search index, reconciliation, DB size. Pings `BETTERSTACK_HB_DAILY`. Daily workflow also runs Playwright browser tests.
- **`scripts/setup-betterstack.sh --host=sea-live`** — creates/manages all monitors and heartbeats via Better Stack API. Idempotent.

### Running manually

```bash
# From devcontainer (needs SSH access to sea-live)
scripts/monitor-safe.sh --host=sea-live
scripts/monitor-deep.sh --host=sea-live
```

## GitHub Actions workflows

Located in **`private/.github/workflows/`** (private repo):

| Workflow | Schedule | What it does |
|---|---|---|
| `monitor-hourly.yml` | `15 * * * *` | SSH into live, run `monitor-safe.sh`, ping hourly heartbeat |
| `monitor-daily.yml` | `30 6 * * *` | SSH into live, run `monitor-deep.sh`, ping daily heartbeat, run Playwright tests |
| `backup.yml` | (see file) | GitHub-side backup workflow |

### Required GitHub secrets

| Secret | Purpose |
|---|---|
| `SSH_MONITOR_KEY` | SSH private key for the `deploy` user on the live server |
| `VPS_HOST` | Server IP address |
| `BETTERSTACK_HB_HOURLY` | Heartbeat URL for hourly monitoring |
| `BETTERSTACK_HB_DAILY` | Heartbeat URL for daily monitoring |
| `LIVE_OJS_URL` | OJS URL for Playwright tests |
| `LIVE_WP_HOME` | WP URL for Playwright tests |
| `CADDY_WP_AUTH_PASS` | Basic auth password for staging WP behind Caddy |

## Server crontab

The live server (`sea-live`) has one cron entry:

```
0 3 * * * /opt/pharkie-ojs-plugins/scripts/backup-ojs-db.sh >> /opt/backups/ojs/backup.log 2>&1 && curl -sf <heartbeat-url> || curl -sf <heartbeat-url>/fail
```

The OJS container has its own internal cron for scheduled tasks (set up in `docker/ojs/entrypoint.sh`).

## Troubleshooting

### Hourly or daily heartbeat down

1. **Check GitHub Actions run logs first:**
   ```bash
   gh run list --workflow=monitor-hourly.yml --limit=5
   gh run view <id> --log-failed
   ```
2. The `[FAIL]` lines show exactly which check failed.
3. If all checks pass but heartbeat is still down: verify `BETTERSTACK_HB_HOURLY` / `BETTERSTACK_HB_DAILY` GitHub secrets.
4. If the workflow isn't running: check the private repo's Actions tab.

### OJS scheduled tasks heartbeat down

See [support-runbook.md § OJS scheduled tasks heartbeat down](support-runbook.md#ojs-scheduled-tasks-heartbeat-down).

### Database backup heartbeat down

Check backup log on the server: `ssh sea-live "tail -20 /opt/backups/ojs/backup.log"`
