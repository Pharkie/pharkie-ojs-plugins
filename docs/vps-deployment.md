# VPS Deployment Guide

Two scripts handle everything: `init-vps.sh` creates the server (Hetzner), firewall, and SSH config. `deploy.sh` clones the repo, builds images, starts the Docker stack, and runs setup. After that, day-to-day code updates are just `git pull` on the VPS — plugins are bind-mounted so PHP picks up changes immediately.

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
| 8082 | Adminer (DB GUI) | localhost only — SSH tunnel required |
| 80 | HTTP (Caddy) | Production with SSL |
| 443 | HTTPS (Caddy) | Production with SSL |

---

## Security hardening

`provision-vps.sh` (run via `scripts/infra/deploy.sh --provision`) applies OS-level hardening automatically. All steps are idempotent.

| What | How | Detail |
|---|---|---|
| SSH hardening | Drop-in config `/etc/ssh/sshd_config.d/hardening.conf` | Password auth disabled, root login key-only, MaxAuthTries 3, idle timeout 10 min |
| fail2ban | Default install + enable | Protects SSH against brute force. Docker services not covered (logs not in host syslog). |
| Unattended upgrades | `unattended-upgrades` package | Security-only patches applied automatically (Ubuntu 24.04 default). |
| Host firewall (ufw) | Allow 22, 80, 443, 8080, 8081; default deny | Backup to Hetzner cloud firewall. |
| DNS fallback | `FallbackDNS=1.1.1.1 8.8.8.8` in `resolved.conf` | Prevents backup failures and service timeouts during upstream DNS outages. |
| Docker log rotation | `daemon.json` (10m x 3 files) | Requires Docker restart (brief container restart). |
| Deploy user | `deploy` user with docker group + limited sudoers | All scripts use `deploy@` (not root). Root key auth kept as emergency backdoor. |
| Container security | `cap_drop: ALL` + per-service `cap_add` | `no-new-privileges` on all. Web (wp, ojs): CHOWN, DAC_OVERRIDE, FOWNER, NET_BIND_SERVICE, SETGID, SETUID. DB: same minus NET_BIND_SERVICE. Caddy: NET_BIND_SERVICE only. |
| Backup scope | OJS DB + WP DB + OJS files volume | All encrypted (AES-256-CBC), 7 daily + 4 weekly retention. |

To verify after provisioning:
```bash
ssh deploy@$IP "systemctl status fail2ban --no-pager; ufw status; cat /etc/ssh/sshd_config.d/hardening.conf; id"
```

---

## Scripts

All scripts run **from your local machine** (or devcontainer) via SSH to the VPS. Nothing needs to be installed on the VPS beyond Docker and Git.

There are two phases: **init** (run once per server) and **deploy** (run every time you ship code).

| Script | Phase | What it does |
|---|---|---|
| `scripts/infra/init-vps.sh` | Init | Creates server, firewall, SSH config ([Hetzner](hetzner-setup.md)) |
| `scripts/infra/provision-vps.sh` | Init | Installs Docker on VPS (called by deploy.sh `--provision`) |
| `scripts/infra/deploy.sh` | Deploy | Pulls code, builds images, starts stack, runs setup |
| `scripts/infra/setup.sh` | Deploy | Configures WP + OJS inside running containers |
| `scripts/monitoring/smoke-test.sh` | Test | Lightweight health checks (curl + WP-CLI via SSH) |
| `scripts/monitoring/load-test.sh` | Test | Performance tests with server monitoring |

### deploy.sh flags

```bash
scripts/infra/deploy.sh [flags]
```

| Flag | Effect |
|---|---|
| `--host=<name>` | Hetzner server name (e.g. `my-staging`). Resolved to IP via `hcloud`. |
| `--provision` | Install Docker on fresh VPS first |
| `--skip-setup` | Don't run setup (just update code + restart) |
| `--skip-build` | Don't rebuild Docker images |
| `--ref=<branch>` | Deploy a specific git ref (default: `main`) |
| `--clean` | Tear down volumes first (fresh databases) |
| `--env-file=<path>` | Copy local .env file to VPS after clone |

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
| `WPOJS_API_KEY_SECRET` | API key OJS validates (env var, not config.inc.php) | **Must match** `WPOJS_API_KEY` |
| `OJS_API_KEY_SECRET` | OJS internal JWT signing key (user API tokens) | Separate from `WPOJS_API_KEY_SECRET` |
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
scripts/infra/init-vps.sh --name=your-server
# For production with SSL: scripts/infra/init-vps.sh --name=your-server --ssl

# 2. Create .env from template
cp .env.example .env.staging
# Edit: set URLs, passwords, salts, API keys

# 3. Provision + deploy (single command — --env-file copies .env after clone)
scripts/infra/deploy.sh --host=your-server --provision --env-file=.env.staging

# 4. Verify (22 checks)
scripts/monitoring/smoke-test.sh --host=your-server
```

For a **full grave-and-repave** (wipe server and start fresh):

```bash
hcloud server delete your-server && hcloud firewall delete your-server-fw
scripts/infra/init-vps.sh --name=your-server
scripts/infra/deploy.sh --host=your-server --provision --clean --env-file=.env.staging
# Wait ~7-10 min for setup + sample data import to complete
scripts/monitoring/smoke-test.sh --host=your-server
```

### Day-to-day code updates

Both plugins are bind-mounted from the repo directory on disk, so `git pull` is all you need — PHP reads files directly, no rebuild required.

SSH to the VPS uses hcloud to resolve the IP at runtime — no SSH config needed:

```bash
# Helper: resolve SSH for a server name
source scripts/lib/resolve-ssh.sh && resolve_ssh "<your-server>"

# Push code to the VPS (the normal workflow)
$SSH_CMD "cd /opt/pharkie-ojs-plugins && git pull"
```

If PHP opcache is serving stale code (rare), restart the containers:

```bash
$SSH_CMD "cd /opt/pharkie-ojs-plugins && git pull && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml restart wp ojs"
```

### When to use deploy.sh

Reserve `deploy.sh` for infrastructure changes — not routine code updates.

```bash
# Dockerfile or compose changes (need image rebuild)
scripts/infra/deploy.sh --host=your-server

# Deploy a specific branch
scripts/infra/deploy.sh --host=your-server --ref=feature-branch

# Full teardown + fresh databases
scripts/infra/deploy.sh --host=your-server --clean
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
   ssh your-server "cd /opt/pharkie-ojs-plugins && \
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
scripts/monitoring/smoke-test.sh --host=your-server
```

Checks (22 total):
1. WP HTTP responds
1b. WP Admin page loads (catches .env permission issues, PHP fatals)
1c. OJS Admin page loads (catches missing journal, PHP fatals)
2. OJS HTTP responds
2b. Adminer responds (localhost:8082, checked via SSH)
3. WP REST API responds
4. OJS plugin ping
5. OJS preflight (auth + compatibility)
6. WP-CLI `test-connection`
7. Required plugins active (5 plugins checked)
8. OJS subscription types configured
8b. Product-to-type mapping validated (all 6 products)
9. Full sync round-trip (create user, sync, verify subscription, expire, anonymise on delete)
10. Reconciliation completes

### Load tests

Performance testing with [`hey`](https://github.com/rakyll/hey). Monitors server resources (CPU, memory, Docker stats) during the test.

```bash
# Install hey (one-time, on machine running the tests)
curl -sfL https://hey-release.s3.us-east-2.amazonaws.com/hey_linux_amd64 \
  -o /usr/local/bin/hey && chmod +x /usr/local/bin/hey

# Standard load (50 concurrent, 500 requests per endpoint)
scripts/monitoring/load-test.sh --host=your-server

# Lighter load (10 concurrent, 100 requests)
scripts/monitoring/load-test.sh --host=your-server --light
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

> **Read this section before your first deploy.** These are real issues encountered during staging — they'll save you time.

## Good to know

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
