# Monitoring

Automated monitoring for the live OJS journal and WP membership site, using three tiers.

## Architecture

```
┌─ Better Stack (external, every 3 min) ──────────────┐
│  HTTP pings, keyword checks, SSL, TCP port, alerts   │
│  Status page: https://uptime.betterstack.com/...     │
└──────────────────────────────────────────────────────┘

┌─ GitHub Actions (hourly, sea-ojs-private repo) ──────┐
│  SSH → monitor-safe.sh: API checks, plugin status,   │
│  Stripe, server resources, container health           │
└──────────────────────────────────────────────────────┘

┌─ GitHub Actions (daily, sea-ojs-private repo) ───────┐
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

## Troubleshooting

**Hourly workflow fails with SSH timeout**: Server may be down or firewall blocking. Check Better Stack for HTTP status. If Better Stack shows up but SSH fails, check Hetzner firewall rules.

**Stripe API key invalid (401)**: Key may have been rotated. Update in `.env.live` on server and `STRIPE_SECRET_KEY` secret in GitHub.

**High disk usage**: Check Docker images/volumes: `docker system df`. Prune if needed: `docker system prune`.

**Container restart count > 0**: Check logs: `docker logs <container>`. Usually OOM or config error after deploy.

**Action Scheduler queue > 50**: May indicate failed sync jobs piling up. Check: `wp action-scheduler list --status=failed --per-page=5`.

**Playwright fails on "pages load within 10 seconds"**: Server may be under load. Check load average in hourly results. If persistent, investigate slow queries or PHP performance.
