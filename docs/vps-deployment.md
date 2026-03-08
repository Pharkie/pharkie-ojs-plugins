# VPS Deployment Guide

Two scripts handle everything: `init-vps.sh` creates the server (Hetzner), firewall, SSH config, and deploy key. `deploy.sh` clones the repo, builds images, starts the Docker stack, and runs setup. After that, day-to-day code updates are just `git pull` on the VPS — plugins are bind-mounted so PHP picks up changes immediately.

For how the Docker stack works (containers, credentials, commands), see the [Docker setup guide](docker-setup.md). For installing plugins without Docker, see [non-Docker setup](non-docker-setup.md).

Related: [Hetzner setup](hetzner-setup.md) · [Email setup](email-setup.md) · [WP plugin management](wp-plugin-management.md)

---

## Server requirements

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 vCPU | 3 vCPU |
| RAM | 2 GB | 4 GB |
| Disk | 25 GB SSD | 40 GB SSD |
| OS | Ubuntu 22.04+ | Ubuntu 24.04 |
| Software | Docker, Docker Compose v2, Git | |
| Access | SSH (root or sudo) | |

OJS and WordPress both run PHP — they benefit from CPU and RAM more than disk. 4 GB RAM gives comfortable headroom for concurrent traffic + sync operations.

### Ports

| Port | Service | When |
|---|---|---|
| 22 | SSH | Always |
| 8080 | WordPress | IP-only / staging |
| 8081 | OJS | IP-only / staging |
| 80 | HTTP (Caddy) | Production with SSL |
| 443 | HTTPS (Caddy) | Production with SSL |

---

## Scripts

All scripts run **from your local machine** (or devcontainer) via SSH to the VPS. Nothing needs to be installed on the VPS beyond Docker and Git.

There are two phases: **init** (run once per server) and **deploy** (run every time you ship code).

| Script | Phase | What it does |
|---|---|---|
| `scripts/init-vps.sh` | Init | Creates server, firewall, SSH config, deploy key ([Hetzner](hetzner-setup.md)) |
| `scripts/provision-vps.sh` | Init | Installs Docker on VPS (called by deploy.sh `--provision`) |
| `scripts/deploy.sh` | Deploy | Pulls code, builds images, starts stack, runs setup |
| `scripts/setup.sh` | Deploy | Configures WP + OJS inside running containers |
| `scripts/smoke-test.sh` | Test | Lightweight health checks (curl + WP-CLI via SSH) |
| `scripts/load-test.sh` | Test | Performance tests with server monitoring |

### deploy.sh flags

```bash
scripts/deploy.sh [flags]
```

| Flag | Effect |
|---|---|
| `--host=<name>` | SSH host alias (default: `sea-staging`) |
| `--provision` | Install Docker on fresh VPS first |
| `--skip-setup` | Don't run setup (just update code + restart) |
| `--skip-build` | Don't rebuild Docker images |
| `--ref=<branch>` | Deploy a specific git ref (default: `main`) |
| `--clean` | Tear down volumes first (fresh databases) |

---

> **The `.env` file is the single source of truth** for all configuration. Get this right and everything else follows. Get it wrong and things fail in confusing ways.

## Environment configuration

The `.env` file on the VPS controls all configuration. It is **not in git** — you create it from `.env.example` and copy it to the server.

> **Watch the matching pairs.** Two pairs of variables must have identical values or the system breaks silently: `WPOJS_API_KEY` = `WPOJS_API_KEY_SECRET`, and `DB_PASSWORD` = `WP_DB_PASSWORD`. This is an artifact of Bedrock and Docker Compose reading the same value under different names.

### Critical variables

| Variable | What | Gotcha |
|---|---|---|
| `WP_HOME` | Full URL to WP (e.g. `http://1.2.3.4:8080`) | Must include port for IP-only access |
| `OJS_BASE_URL` | Full URL to OJS (e.g. `http://1.2.3.4:8081`) | Must include port for IP-only access |
| `WP_ADMIN_PASSWORD` | WordPress admin password | **Required — no default.** Setup fails if missing. |
| `OJS_ADMIN_PASSWORD` | OJS admin password | **Required — no default.** Setup fails if missing. |
| `WPOJS_API_KEY` | API key WP sends to OJS | **Must match** `WPOJS_API_KEY_SECRET` |
| `WPOJS_API_KEY_SECRET` | API key OJS validates | **Must match** `WPOJS_API_KEY` |
| `DB_PASSWORD` | WP database password (Bedrock reads this) | **Must match** `WP_DB_PASSWORD` |
| `WP_DB_PASSWORD` | WP database password (Docker Compose reads this) | **Must match** `DB_PASSWORD` |
| Auth salts | WordPress security salts | Generate unique random values per environment |

### For SSL (production)

| Variable | What |
|---|---|
| `CADDY_WP_DOMAIN` | Domain for WordPress (e.g. `wp.example.org`) |
| `CADDY_OJS_DOMAIN` | Domain for OJS (e.g. `journal.example.org`) |

---

## Deployment workflow

### First deploy (fresh server)

```bash
# 1. Create server + infrastructure (Hetzner)
scripts/init-vps.sh --name=your-server
# For production with SSL: scripts/init-vps.sh --name=your-server --ssl

# 2. Create .env from template
cp .env.example .env.staging
# Edit: set URLs, passwords, salts, API keys

# 3. Copy .env to server
scp .env.staging your-server:/opt/wp-ojs-sync/.env

# 4. Provision + deploy (installs Docker, clones repo, builds, starts, runs setup)
scripts/deploy.sh --host=your-server --provision

# 5. Copy non-git files (if applicable)
#    Paid WP plugins (licensed, can't be in git):
rsync -az -e ssh wordpress/paid-plugins/ your-server:/opt/wp-ojs-sync/wordpress/paid-plugins/
#    OJS import XML (too large for git):
scp "data export/ojs-import-clean.xml" "your-server:/opt/wp-ojs-sync/data export/"

# 6. Re-deploy with data files present
scripts/deploy.sh --host=your-server --clean

# 7. Verify
scripts/smoke-test.sh --host=your-server
```

### Day-to-day code updates

Both plugins are bind-mounted from the repo directory on disk, so `git pull` is all you need — PHP reads files directly, no rebuild required.

```bash
# Push code to the VPS (the normal workflow)
ssh your-server "cd /opt/wp-ojs-sync && git pull"
```

If PHP opcache is serving stale code (rare), restart the containers:

```bash
ssh your-server "cd /opt/wp-ojs-sync && git pull && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml restart wp ojs"
```

### When to use deploy.sh

Reserve `deploy.sh` for infrastructure changes — not routine code updates.

```bash
# Dockerfile or compose changes (need image rebuild)
scripts/deploy.sh --host=your-server

# Deploy a specific branch
scripts/deploy.sh --host=your-server --ref=feature-branch

# Full teardown + fresh databases
scripts/deploy.sh --host=your-server --clean
```

Use `deploy.sh` when:
- Dockerfiles change (new PHP extensions, base image updates)
- Docker Compose changes (new services, volumes, ports)
- Setup scripts change and need to re-run
- You want a clean slate (`--clean`)

---

## Adding SSL (production)

> SSL is optional for staging but required for production. Without HTTPS, API keys are sent in cleartext.

When you have domains pointing at the server:

1. Add DNS A records pointing to the server IP
2. Set `CADDY_WP_DOMAIN` and `CADDY_OJS_DOMAIN` in `.env`
3. Open ports 80 and 443 in the server firewall
4. Start with the Caddy overlay:
   ```bash
   ssh your-server "cd /opt/wp-ojs-sync && \
     docker compose -f docker-compose.yml \
       -f docker-compose.staging.yml \
       -f docker-compose.caddy.yml up -d"
   ```

Caddy handles Let's Encrypt certificate provisioning and renewal automatically.

---

## Testing a deployment

### Smoke tests

Lightweight health checks — no Node or Playwright needed on the VPS. Runs from your local machine via SSH + curl.

```bash
scripts/smoke-test.sh --host=your-server
```

Checks (17 total):
1. WP HTTP responds
1b. WP Admin page loads (catches .env permission issues, PHP fatals)
1c. OJS Admin page loads (catches missing journal, PHP fatals)
2. OJS HTTP responds
3. WP REST API responds
4. OJS plugin ping
5. OJS preflight (auth + compatibility)
6. WP-CLI `test-connection`
7. Required plugins active (5 plugins checked)
8. OJS subscription types configured
9. Full sync round-trip (create user, sync to OJS, verify subscription, cleanup)
10. Reconciliation completes

### Load tests

Performance testing with [`hey`](https://github.com/rakyll/hey). Monitors server resources (CPU, memory, Docker stats) during the test.

```bash
# Install hey (one-time, on machine running the tests)
curl -sfL https://hey-release.s3.us-east-2.amazonaws.com/hey_linux_amd64 \
  -o /usr/local/bin/hey && chmod +x /usr/local/bin/hey

# Standard load (50 concurrent, 500 requests per endpoint)
scripts/load-test.sh --host=your-server

# Lighter load (10 concurrent, 100 requests)
scripts/load-test.sh --host=your-server --light
```

Endpoints tested:
1. OJS journal homepage
2. OJS article page
3. OJS API preflight
4. WP homepage
5. WP REST API

Pass criteria: p95 latency <= 2000ms, zero server errors. Reports peak CPU load and memory usage during the test.

`hey` generates harder-than-real load (no think time, no browser rendering, no connection reuse between requests). If the server passes these tests, it will handle real traffic comfortably.

---

## Useful commands

All commands assume SSH access to the VPS. Replace `your-server` with your SSH host alias.

```bash
# View logs (all containers)
ssh your-server "cd /opt/wp-ojs-sync && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml logs -f --tail=50"

# Check container status
ssh your-server "cd /opt/wp-ojs-sync && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml ps"

# Run WP-CLI commands
ssh your-server "cd /opt/wp-ojs-sync && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  exec wp wp --allow-root ojs-sync status"

# Access OJS database
ssh your-server "cd /opt/wp-ojs-sync && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  exec ojs-db mysql -u ojs -p ojs"
```

---

> **Read this section before your first deploy.** These are real issues we hit during staging — they'll save you time.

## Gotchas

- **`.env` is not in git.** You must copy it to the VPS separately. The deploy script checks for it and fails with instructions if missing.
- **Docker creates directories for missing file mounts.** If a compose bind mount references a file that doesn't exist (e.g. OJS import XML), Docker creates it as an empty directory. You must `down -v` and `up` again after adding the missing file. The deploy script handles this by syncing non-git files before starting containers.
- **First image build takes ~3 minutes** on a 3 vCPU VPS (compiling PHP extensions). Subsequent builds use Docker cache.
- **OJS base image is amd64 only.** ARM servers won't work (`platform: linux/amd64` in compose).
- **Composer install runs automatically** on first WP setup when WordPress core files are missing (Bedrock downloads WP core + plugins via Composer).
- **Paid plugins must be copied separately** — licensed code can't be in a public git repo. Use `rsync` to sync `wordpress/paid-plugins/` to the VPS before running setup. See [WP plugin management](wp-plugin-management.md).
- **`scp` creates files with 600 permissions.** Apache/www-data can't read them. The deploy script runs `chmod 644` on `.env` automatically. Do not change this to 600 — WP-CLI (root) will work but the web server won't, and you'll only catch it via the smoke test's admin page check.
- **No default passwords.** `WP_ADMIN_PASSWORD` and `OJS_ADMIN_PASSWORD` must be set in `.env`. Both `docker-compose.yml` and the setup scripts fail loudly if they're missing or empty. The deploy script validates all required env vars before starting containers.
- **Bulk sync is a manual step.** After setup, existing WP members are not automatically synced to OJS. Run `wp ojs-sync sync --bulk --dry-run` to preview, then `wp ojs-sync sync --bulk --yes` to execute (~5-10 min for ~700 members). The `--bulk` flag is required to prevent accidental full sync. This is deliberate — it's a one-time operation that should be reviewed before running.
- **HPOS and sample data.** When loading sample data (`--with-sample-data`), the setup script temporarily disables HPOS (High-Performance Order Storage), seeds subscriptions via raw SQL into `wp_posts`, then syncs to HPOS and re-enables it. Without this, WooCommerce can't see the seeded subscriptions.
