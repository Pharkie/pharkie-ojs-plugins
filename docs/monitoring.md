# Monitoring

Automated monitoring for the live OJS journal and WP membership site, using three tiers.

## Architecture

```
┌─ Better Stack Uptime (external, every 3 min) ───────┐
│  HTTP pings, keyword checks, SSL, TCP port, alerts   │
│  Status page: https://uptime.betterstack.com/...     │
└──────────────────────────────────────────────────────┘

┌─ Better Stack Collector (on-server, continuous) ─────┐
│  eBPF agent: host/container metrics, DB discovery     │
│  Vector pipeline: logs, metrics, traces → Telemetry   │
│  Pre-built dashboards, threshold alerts               │
└──────────────────────────────────────────────────────┘

┌─ GitHub Actions (hourly, ojs-sea-private repo) ──────┐
│  SSH → monitor-safe.sh: API checks, plugin status,   │
│  Stripe, server resources, container health           │
└──────────────────────────────────────────────────────┘

┌─ GitHub Actions (daily, ojs-sea-private repo) ───────┐
│  SSH → monitor-deep.sh: sync round-trip, backups,     │
│  search index, reconciliation, DB sizes               │
│  Playwright → live-readonly.spec.ts: browser checks   │
└──────────────────────────────────────────────────────┘
```

## Tier 1: Better Stack (external HTTP monitoring)

**Dashboard**: Better Stack → Monitors

**What's monitored** (9 monitors, 3-min interval):

| Monitor | Type | What it checks |
|---------|------|----------------|
| WP Homepage | HTTP | Returns 200 |
| WP REST API | HTTP | `/wp-json/` returns 200 |
| WP Admin | HTTP | `/wp/wp-admin/` returns 200/302 |
| OJS Homepage | HTTP | Returns 200 |
| OJS Journal | HTTP | `/index.php/ea/` returns 200 |
| OJS Article Content | Keyword | "Existential Analysis" present on journal page |
| OJS Login Page | Keyword | "Sign In" present on login page |
| Stripe Webhook Route | Keyword absence | "Not Found" does NOT appear (route exists) |
| HTTPS Port | TCP | Port 443 reachable |

**Heartbeats** (5 heartbeats, expect periodic pings from cron jobs/workflows):

| Heartbeat | Period | Grace | What it catches |
|-----------|--------|-------|----------------|
| SEA: Hourly monitoring | 75 min | 30 min | GitHub Actions hourly workflow stopped running |
| SEA: Daily monitoring | 25 hours | 2 hours | GitHub Actions daily workflow stopped running |
| SEA: Database backup | 25 hours | 2 hours | VPS backup cron failed or stopped |
| SEA: OJS scheduled tasks | 75 min | 30 min | OJS cron not running (container issue) |
| SEA: GitHub backup pull | 25 hours | 2 hours | Backup pull workflow stopped |

Each job pings its heartbeat URL on success, or `/fail` on failure. If no ping arrives within period + grace, Better Stack creates an incident (email + SMS, no phone calls).

**Built-in**: SSL expiry alerts, response time tracking, free status page.

### Setup

```bash
# Set your Better Stack API token
export BETTERSTACK_API_TOKEN="your-token-here"

# Preview what will be created
scripts/setup-betterstack.sh --host=sea-live --dry-run

# Create monitors
scripts/setup-betterstack.sh --host=sea-live

# Delete all SEA monitors (if needed)
scripts/setup-betterstack.sh --host=sea-live --delete-all
```

The script is idempotent — running it twice won't create duplicates.

### Modifying monitors

- **Add a monitor**: Add a `create_monitor` call in `scripts/setup-betterstack.sh`, then re-run
- **Edit a monitor**: Easiest via the Better Stack dashboard
- **Delete a monitor**: Dashboard, or `--delete-all` + re-run

## Tier 2: GitHub Actions hourly checks

**Workflow**: `sea-ojs-private/.github/workflows/monitor-hourly.yml`
**Script**: `scripts/monitor-safe.sh`

Runs via SSH — non-mutating, safe to run frequently.

### What's checked

**HTTP & API**: WP homepage, WP admin, WP REST API, OJS homepage, OJS admin, OJS plugin ping, OJS preflight, WP-CLI test-connection

**Plugins**: All 5 required WP plugins active (WooCommerce, WCS, WC Memberships, UM, wpojs-sync)

**Subscription config**: OJS subscription types exist, product-to-type mappings valid

**Stripe**:
- Stripe plugin active in OJS
- Stripe API key valid (balance endpoint check)
- Stripe webhook endpoint reachable (returns 400, not 404)
- No stale queued payments (>24h old)

**Server resources**:
- Load average (alert if 5-min > 2× CPU count)
- Memory (alert if available < 256MB)
- Swap (alert if used > 100MB)
- Disk space (alert if > 85%)

**Container health**:
- All Docker containers running
- Restart counts (alert if > 0)
- PHP fatal errors in logs (last hour)
- OOM detection in dmesg

**WP health**:
- Action Scheduler queue (alert if > 50 pending)
- Adminer accessible

### Manual run

```bash
# From devcontainer
scripts/monitor-safe.sh --host=sea-live

# Trigger on GitHub
gh workflow run monitor-hourly.yml -R Pharkie/sea-ojs-private
```

## Tier 3: GitHub Actions daily deep checks

**Workflow**: `sea-ojs-private/.github/workflows/monitor-daily.yml`
**Script**: `scripts/monitor-deep.sh` + `e2e/tests/monitoring/live-readonly.spec.ts`

### What's checked (in addition to all hourly checks)

**Sync round-trip**: Creates a test user, syncs to OJS, verifies subscription, cleans up

**Backup health**: Cron installed, encryption key present, latest backup age and size

**Search index**: Object count, HTTP search test with a real author name

**Reconciliation**: `wp ojs-sync reconcile` completes successfully

**Database sizes**: Alert if either DB exceeds 2GB

**OJS scheduled tasks**: Alert if last run > 25 hours ago

**Playwright browser checks** (read-only, no mutations):
- Journal homepage loads and shows articles
- Article page renders title and abstract
- Archive page lists issues
- Login page renders form
- Paywalled articles show prices for anonymous users
- Open-access content renders
- No browser console errors
- All pages load within 10 seconds

### Manual run

```bash
# SSH checks from devcontainer
scripts/monitor-deep.sh --host=sea-live

# Playwright checks
LIVE_WP_HOME=https://community.existentialanalysis.org.uk \
LIVE_OJS_URL=https://journal.existentialanalysis.org.uk \
  npx playwright test --config=playwright.monitor.config.ts

# Trigger on GitHub
gh workflow run monitor-daily.yml -R Pharkie/sea-ojs-private
```

## Alerts

- **Better Stack**: Sends email on downtime/recovery (configurable in dashboard)
- **GitHub Actions**: Sends email on workflow failure to the repo owner
- No notifications on success — alerts are on state changes only (Better Stack) or failures only (GitHub)

## Adding a new check

### To Better Stack
Add a `create_monitor` call in `scripts/setup-betterstack.sh` and re-run.

### To hourly checks
Add a check section in `scripts/monitor-safe.sh` using the `pass`/`fail` functions:
```bash
RESULT=$(remote "some-command")
if [ "$RESULT" = "expected" ]; then
  pass "Description of check"
else
  fail "Description of failure" "$RESULT"
fi
```

### To daily checks
Add a check section in `scripts/monitor-deep.sh` (same pattern).

### To Playwright checks
Add a test in `e2e/tests/monitoring/live-readonly.spec.ts`. **Constraint**: must be read-only — no form submissions, no user creation, no state modification.

## Tier 4: Better Stack Collector (server telemetry)

**Dashboard**: Better Stack → Telemetry → Dashboards

The Better Stack Collector runs on the live server as two Docker containers, collecting host/container metrics, logs, and traces via eBPF. Data is shipped to Better Stack Telemetry for dashboards and alerting.

### Architecture

```
better-stack-ebpf (host network, privileged)
├── OBI — eBPF observer, intercepts kernel-level traffic (no app credentials needed)
├── Node Agent — host CPU, memory, disk, network metrics
├── Docker Probe — discovers containers, maps PIDs to container names
├── Cluster Agent — discovers databases (MySQL auto-detected)
└── Pushes metrics → localhost:39090 (prometheus remote write)

better-stack-collector (host network)
├── Vector — data pipeline, receives metrics/logs, ships to Better Stack
├── Updater — downloads config from Better Stack API, reloads Vector
├── Watchmon — health monitoring, restarts Vector if stuck
└── Sends to → s2319705.eu-fsn-3.betterstackdata.com
```

### Deployment

Both containers are managed by `docker-compose.collector.yml` (project name: `better-stack`), separate from the main app stack. The collector joins `pharkie-ojs-plugins_sea-net` so it can reach DB containers.

```bash
# Deploy / update (from /opt/pharkie-ojs-plugins on live):
docker compose -f docker-compose.collector.yml up -d

# View logs:
docker exec better-stack-collector tail -f /var/lib/better-stack/logs/collector/vector.out.log
docker exec better-stack-collector cat /var/lib/better-stack/logs/collector/updater.out.log

# Check config promotion:
docker exec better-stack-collector ls -la /versions/current
```

`COLLECTOR_SECRET` is in `.env` on live. The secret authenticates with Better Stack's API to download config and ship telemetry.

### Source

- **Name**: SEA Server
- **Platform**: collector
- **Region**: eu-fsn-3

### Pre-built dashboards

Recreating the source in Better Stack auto-generates pre-built dashboards (Host overview, Containers, etc.). If dashboards show "source isn't eligible", the container resource metrics haven't started flowing yet — wait ~5 minutes after collector start, then retry.

### Key design decisions

- **Collector, not standalone Vector**: The Collector (Docker + eBPF) is what pre-built dashboards expect. Standalone Vector was tried first and removed. Don't reinstall it.
- **Both containers use host networking**: Vector binds to `127.0.0.1:39090` for prometheus remote write. The eBPF agent pushes metrics to this port. Both must be on the host network for this localhost communication to work. Bridge networking with port mapping does NOT work because Docker proxies to the container's bridge IP, which Vector ignores.
- **Separate compose project**: Uses `name: better-stack` to avoid orphan warnings with the main `pharkie-ojs-plugins` project.
- **DB discovery works via eBPF**: The cluster-agent (in the eBPF container, host network) can reach Docker bridge networks through the host routing table. No need to put the collector on `sea-net`.

## Why not do everything in Better Stack?

Evaluated March 2026. Better Stack has a Playwright monitor type and could theoretically replace the GitHub Actions browser checks, but **30 of 41 checks require SSH access to the server** (WP-CLI commands, Docker container inspection, database queries, backup verification, sync round-trips, server resource checks). Better Stack can only do external HTTP/keyword checks — it can't SSH into infrastructure.

| Capability | Better Stack (free) | Better Stack (paid) | GitHub Actions |
|------------|-------------------|-------------------|----------------|
| HTTP ping / keyword | Yes (9 monitors) | Yes | Yes |
| SSL expiry alerts | Yes | Yes | No |
| Status page | Yes | Yes | No |
| Playwright browser tests | No | $1/100 exec minutes | Yes (free tier) |
| SSH into server | No | No | **Yes** |
| WP-CLI / Docker checks | No | No | **Yes** |
| Sync round-trip test | No | No | **Yes** |
| Backup verification | No | No | **Yes** |
| Server resource checks | No | No | **Yes** |

**Decision**: Use both. Better Stack free tier for fast external pings (3-min interval, status page, SSL). GitHub Actions for everything that needs server access. Moving the 10 Playwright tests to Better Stack would cost ~$0.10–$2.40/month depending on frequency, but GitHub Actions runs them for free within the 2,000 min/month private repo allowance. Not worth the complexity of splitting browser tests across two systems for negligible benefit.

**Revisit if**: GitHub Actions free minutes become insufficient, or we need sub-hourly deep checks, or Better Stack adds SSH/agent-based monitoring.

## GitHub Actions minutes budget

| Workflow | Frequency | Per run | Monthly |
|----------|-----------|---------|---------|
| Hourly safe | 24/day | ~1.5 min | ~1,080 min |
| Daily deep | 1/day | ~6 min | ~180 min |
| **Total** | | | **~1,260 min** |

Free tier limit: 2,000 min/month for private repos.

## Secrets (sea-ojs-private repo)

| Secret | Description |
|--------|-------------|
| `SSH_PRIVATE_KEY` | `~/.ssh/hetzner` private key |
| `HCLOUD_TOKEN` | Hetzner Cloud API token |
| `KNOWN_HOSTS` | `ssh-keyscan` output for server |
| `STRIPE_SECRET_KEY` | Live Stripe secret key |
| `BETTERSTACK_API_TOKEN` | Better Stack API token |
| `LIVE_WP_HOME` | WP URL for Playwright |
| `LIVE_OJS_URL` | OJS URL for Playwright |
| `BETTERSTACK_HB_HOURLY` | Heartbeat URL for hourly workflow |
| `BETTERSTACK_HB_DAILY` | Heartbeat URL for daily workflow |
| `BETTERSTACK_HB_BACKUP` | Heartbeat URL for VPS backup cron |
| `BETTERSTACK_HB_BACKUP_PULL` | Heartbeat URL for GitHub backup pull |

**On live server (`.env`)**:

| Variable | Description |
|----------|-------------|
| `BETTERSTACK_HB_OJS_CRON` | Heartbeat URL for OJS scheduled tasks |
| `COLLECTOR_SECRET` | Better Stack Collector authentication secret |

## Troubleshooting

**Hourly workflow fails with SSH timeout**: Server may be down or firewall blocking. Check Better Stack for HTTP status. If Better Stack shows up but SSH fails, check Hetzner firewall rules.

**Stripe API key invalid (401)**: Key may have been rotated. Update in `.env.live` on server and `STRIPE_SECRET_KEY` secret in GitHub.

**High disk usage**: Check Docker images/volumes: `docker system df`. Prune if needed: `docker system prune`.

**Container restart count > 0**: Check logs: `docker logs <container>`. Usually OOM or config error after deploy.

**Action Scheduler queue > 50**: May indicate failed sync jobs piling up. Check: `wp action-scheduler list --status=failed --per-page=5`.

**Playwright fails on "pages load within 10 seconds"**: Server may be under load. Check load average in hourly results. If persistent, investigate slow queries or PHP performance.

**Collector not sending data**: Check `docker exec better-stack-collector cat /var/lib/better-stack/logs/collector/updater.out.log` — look for "Successfully promoted to current". If config hasn't promoted, wait ~5–10 minutes after start. Check `docker exec better-stack-collector ls -la /versions/current` for the config symlink.

**Dashboard says "source isn't eligible"**: Container resource metrics haven't flowed yet. Check dockerprobe is detecting containers: `docker exec better-stack-ebpf cat /var/lib/better-stack/logs/ebpf/dockerprobe.out.log`. If it shows "Mapped N PIDs to container", the data pipeline is working — wait a few more minutes. If the source was recreated in Better Stack, verify the collector config still points to the correct ingesting host.

**MySQL "Access denied" in cluster-agent logs**: Expected — the eBPF approach intercepts traffic at kernel level and doesn't need DB credentials. The cluster-agent probes are opportunistic. MySQL metrics come from eBPF/OBI, not from direct connections.
