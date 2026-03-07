# Deployment Guide

How to deploy the WP-OJS stack to a VPS using Docker. Covers requirements, scripts, configuration, testing, and SSL.

For non-Docker setup (native OJS + WP servers), see [`non-docker-setup.md`](non-docker-setup.md).

---

## Server requirements

### Minimum spec

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 2 vCPU | 3 vCPU |
| RAM | 2 GB | 4 GB |
| Disk | 25 GB SSD | 40 GB SSD |
| OS | Ubuntu 22.04+ | Ubuntu 24.04 |

OJS and WordPress both run PHP — they benefit from CPU and RAM more than disk. 4 GB RAM gives comfortable headroom for concurrent traffic + sync operations.

### Software

- **Docker** and **Docker Compose** (v2)
- **Git** (to clone the repo)
- **SSH access** (root or sudo)

### Network

| Port | Service | When |
|---|---|---|
| 22 | SSH | Always |
| 8080 | WordPress | IP-only / staging |
| 8081 | OJS | IP-only / staging |
| 80 | HTTP (Caddy) | Production with SSL |
| 443 | HTTPS (Caddy) | Production with SSL |

---

## Architecture

```
VPS
├── wp        — WordPress (Apache + PHP 8.2)     → port 8080
├── wp-db     — MariaDB 10.11 (WP database)
├── ojs       — OJS 3.5 (Apache + PHP)           → port 8081
├── ojs-db    — MariaDB 10.11 (OJS database)
└── caddy     — Reverse proxy + SSL (optional)    → ports 80/443
```

All services run as Docker containers via Docker Compose. Three compose files:

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base services (used everywhere) |
| `docker-compose.staging.yml` | Staging/prod overrides: exposes ports 8080/8081 directly |
| `docker-compose.caddy.yml` | Optional SSL overlay: adds Caddy for HTTPS with real domains |

---

## Scripts

All deployment scripts run **from your local machine** (or devcontainer) via SSH to the VPS. Nothing needs to be installed on the VPS beyond Docker and Git.

| Script | What it does | Where it runs |
|---|---|---|
| `scripts/provision-vps.sh` | Installs Docker on a fresh Ubuntu VPS | On VPS (via deploy.sh) |
| `scripts/deploy.sh` | Pulls code, builds images, starts stack, runs setup | From local → VPS via SSH |
| `scripts/setup.sh` | Configures WP + OJS inside running containers | Inside containers (via deploy.sh) |
| `scripts/smoke-test.sh` | Lightweight health checks (curl + WP-CLI via SSH) | From local → VPS via SSH |
| `scripts/load-test.sh` | Performance tests with server monitoring | From local → VPS via SSH |

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

## Environment configuration

The `.env` file on the VPS controls all configuration. It is **not in git** — you create it from `.env.example` and copy it to the server.

### Critical variables

| Variable | What | Gotcha |
|---|---|---|
| `WP_HOME` | Full URL to WP (e.g. `http://1.2.3.4:8080`) | Must include port for IP-only access |
| `OJS_BASE_URL` | Full URL to OJS (e.g. `http://1.2.3.4:8081`) | Must include port for IP-only access |
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
# 1. Prepare .env from template
cp .env.example .env.staging
# Edit: set URLs, passwords, salts, API keys

# 2. Copy .env to server
scp .env.staging your-server:/opt/wp-ojs-sync/.env

# 3. Provision + deploy
scripts/deploy.sh --host=your-server --provision

# 4. Copy non-git files (if applicable)
#    Paid WP plugins (licensed, can't be in git):
rsync -az -e ssh wordpress/paid-plugins/ your-server:/opt/wp-ojs-sync/wordpress/paid-plugins/
#    OJS import XML (too large for git):
scp "data export/ojs-import-clean.xml" "your-server:/opt/wp-ojs-sync/data export/"

# 5. Re-deploy with data files present
scripts/deploy.sh --host=your-server --clean
```

### Code updates

```bash
# Deploy latest main branch
scripts/deploy.sh --host=your-server

# Deploy a specific branch
scripts/deploy.sh --host=your-server --ref=feature-branch

# Quick restart (no rebuild, no setup)
scripts/deploy.sh --host=your-server --skip-build --skip-setup
```

---

## Testing a deployment

### Smoke tests

Lightweight health checks — no Node or Playwright needed on the VPS. Runs from your local machine via SSH + curl.

```bash
scripts/smoke-test.sh --host=your-server
```

Checks (15 total):
1. WP HTTP responds
2. OJS HTTP responds
3. WP REST API responds
4. OJS plugin ping
5. OJS preflight (auth + compatibility)
6. WP-CLI `test-connection`
7. Required plugins active (5 plugins checked)
8. OJS subscription types configured
9. Full sync round-trip (create user, sync to OJS, verify, cleanup)
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

## Adding SSL (production)

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

## Gotchas

- **`.env` is not in git.** You must copy it to the VPS separately. The deploy script checks for it and fails with instructions if missing.
- **Docker creates directories for missing file mounts.** If a compose bind mount references a file that doesn't exist (e.g. OJS import XML), Docker creates it as an empty directory. You must `down -v` and `up` again after adding the missing file. The deploy script handles this by syncing non-git files before starting containers.
- **First image build takes ~3 minutes** on a 3 vCPU VPS (compiling PHP extensions). Subsequent builds use Docker cache.
- **OJS base image is amd64 only.** ARM servers won't work (`platform: linux/amd64` in compose).
- **Composer install runs automatically** on first WP setup when WordPress core files are missing (Bedrock downloads WP core + plugins via Composer).
- **Paid plugins must be copied separately** — licensed code can't be in a public git repo. Use `rsync` to sync `wordpress/paid-plugins/` to the VPS before running setup.
- **`scp` creates files with 600 permissions.** Apache/www-data can't read them. The deploy script runs `chmod 644` on `.env` automatically.

---

## Provider notes: Hetzner Cloud (recommended)

We use [Hetzner Cloud](https://www.hetzner.com/cloud/) for staging and production. Good value, EU data centres, straightforward API.

### Recommended plan

**CPX22** — 3 vCPU (AMD), 4 GB RAM, 80 GB SSD. Runs the full stack comfortably with headroom for sync operations and traffic spikes. ~7 EUR/month.

### Useful hcloud CLI commands

The [`hcloud` CLI](https://github.com/hetznercloud/cli) manages servers from your terminal. Set `HCLOUD_TOKEN` as an environment variable.

```bash
# Create server with SSH key
hcloud server create \
  --name my-server \
  --type cpx22 \
  --image ubuntu-24.04 \
  --location nbg1 \
  --ssh-key my-key-name

# Create firewall (SSH + WP + OJS)
hcloud firewall create --name my-fw
hcloud firewall add-rule my-fw --direction in --protocol tcp --port 22 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 --description SSH
hcloud firewall add-rule my-fw --direction in --protocol tcp --port 8080 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 --description WP
hcloud firewall add-rule my-fw --direction in --protocol tcp --port 8081 \
  --source-ips 0.0.0.0/0 --source-ips ::/0 --description OJS
hcloud firewall apply-to-resource my-fw --type server --server my-server

# For production, also open 80 + 443 for Caddy SSL

# Upload SSH key to Hetzner
hcloud ssh-key create --name my-key --public-key-from-file ~/.ssh/my-key.pub

# Rebuild server (wipes everything, fresh OS)
hcloud server rebuild --image ubuntu-24.04 my-server
```

### Hetzner gotcha

`hcloud server rebuild` does **not** re-inject SSH keys from your Hetzner account. Use `--user-data-from-file` with cloud-init to inject your key:

```bash
hcloud server rebuild --image ubuntu-24.04 \
  --user-data-from-file <(cat <<EOF
#cloud-config
ssh_authorized_keys:
  - $(cat ~/.ssh/my-key.pub)
EOF
) my-server
```

### Locations

| Code | Location |
|---|---|
| `nbg1` | Nuremberg, Germany |
| `fsn1` | Falkenstein, Germany |
| `hel1` | Helsinki, Finland |
| `ash` | Ashburn, USA |
| `hil` | Hillsboro, USA |
