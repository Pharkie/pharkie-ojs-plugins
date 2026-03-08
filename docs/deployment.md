# Deployment Guide

> **This guide covers deploying to a VPS using Docker.** For local dev setup, see [Docker setup](../docker/README.md). For installing the plugins on existing non-Docker servers, see [non-Docker setup](non-docker-setup.md).

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

All services run as Docker containers via Docker Compose.

### Why Docker?

The plugin source code lives in the git repo and is **bind-mounted** directly into the running containers. This means:

- **Updating code is just `git pull`.** Both plugins (WP and OJS) are read from disk by the containers in real time. No rebuild, no restart, no deployment pipeline — PHP picks up the new files immediately.
- **Dev and production run the same stack.** Same Dockerfiles, same compose config, same bind mounts. What works locally works on the VPS.
- **The entire environment is reproducible.** `docker compose down -v && docker compose up -d` gives you a clean slate. No debugging stale state on a snowflake server.

Three compose files:

| File | Purpose |
|---|---|
| `docker-compose.yml` | Base services (used everywhere) |
| `docker-compose.staging.yml` | Staging/prod overrides: exposes ports 8080/8081 directly |
| `docker-compose.caddy.yml` | Optional SSL overlay: adds Caddy for HTTPS with real domains |

---

## Scripts

All scripts run **from your local machine** (or devcontainer) via SSH to the VPS. Nothing needs to be installed on the VPS beyond Docker and Git.

There are two phases: **init** (run once per server) and **deploy** (run every time you ship code).

| Script | Phase | What it does |
|---|---|---|
| `scripts/init-vps.sh` | Init | Creates server, firewall, SSH config, deploy key (Hetzner) |
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

## Managing WP plugins

WordPress uses [Bedrock](https://roots.io/bedrock/), which manages plugins via Composer instead of the WP admin installer. Plugins not in `wordpress/composer.json` won't exist after a fresh deploy — `composer install` is what populates the plugins directory.

### Adding a free plugin (from wordpress.org)

Add to `wordpress/composer.json`:

```json
"require": {
    "wpackagist-plugin/plugin-name": "^1.0",
    ...
}
```

Then run `composer update` inside the WP container:

```bash
ssh your-server "cd /opt/wp-ojs-sync && \
  docker compose -f docker-compose.yml -f docker-compose.staging.yml \
  exec wp composer update --no-dev --working-dir=/var/www/html"
```

Or commit the updated `composer.json` + `composer.lock` and `git pull` on the VPS.

### Adding a paid plugin (not on wpackagist)

Paid plugins can't be pulled from a public registry. Instead:

1. Drop the plugin folder in `wordpress/paid-plugins/`
2. Add a `path` repository in `wordpress/composer.json`:
   ```json
   "repositories": [
       {
           "type": "path",
           "url": "paid-plugins/my-paid-plugin",
           "options": { "symlink": false }
       }
   ]
   ```
3. Add the `require` entry:
   ```json
   "require": {
       "vendor/my-paid-plugin": "1.0.0",
       ...
   }
   ```
4. `rsync` the paid plugins to the VPS (the deploy script does this automatically if they exist locally)

See the existing entries for WooCommerce Subscriptions and WooCommerce Memberships as examples.

### Important for production

Any plugin active on the live WP site must be added to `composer.json` before production deployment. If it's missing, the plugin won't be installed and anything depending on it will break. Audit the live plugin list against `composer.json` before going live.

---

> **SSL is optional for staging** but required for production. Without HTTPS, API keys are sent in cleartext.

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

> **Email is not required for the sync to work.** Members log in with their WP password (synced via hash). Email is needed for OJS editorial workflows (password resets, reviewer notifications, etc.).

## Email setup

Both OJS and WP need to send transactional emails (password resets, editorial notifications). Docker containers can't send email directly — you need an external SMTP relay. Note: email is no longer a prerequisite for bulk sync (password hashes are synced instead of welcome emails).

Hetzner blocks port 25 (outbound SMTP) by default, but port 587 (submission with TLS) works fine, which is what all transactional email services use.

### Recommended: Resend

[Resend](https://resend.com) — modern transactional email service, built on Amazon SES. 3,000 emails/month free (100/day). Clean API, good docs, supports standard SMTP so it works with OJS's built-in config without code changes.

Other options:

| Service | Free tier | Notes |
|---|---|---|
| **Postmark** | 100 emails/month | Best deliverability reputation |
| **Mailgun** | 1,000 emails/month (first 3 months) | Flexible, good logs |
| **Brevo** (ex-Sendinblue) | 300 emails/day | Generous free tier |
| **Amazon SES** | ~$0.10 per 1,000 | Cheapest at scale, more setup |

For ~700 members with occasional password resets and editorial notifications, any of these work.

### OJS email configuration

OJS SMTP is configured via `.env` — the plumbing is already in docker-compose.yml:

```
OJS_SMTP_ENABLED=On
OJS_SMTP_HOST=smtp.resend.com
OJS_SMTP_PORT=587
OJS_SMTP_AUTH=tls
OJS_SMTP_USER=resend
OJS_SMTP_PASSWORD=re_your_api_key_here
OJS_MAIL_FROM=journal@yourdomain.org
```

### WP email configuration

WP email is typically handled by an SMTP plugin (WP Mail SMTP, FluentSMTP, etc.) pointed at the same service. If the live WP already sends email via a relay, reuse those credentials.

### DNS records (required for deliverability)

Add these DNS records for your sending domain:

- **SPF** — `TXT` record authorizing the service to send on your behalf
- **DKIM** — `TXT` record for cryptographic email signing
- **DMARC** — `TXT` record with your policy (start with `p=none`)

Each service provides the exact records to add. Without these, emails land in spam.

### Testing email delivery

Test before going live:

1. Send a password reset to yourself — check it arrives, check spam score
2. Verify DKIM passes (Gmail: "Show original" → look for `dkim=pass`)

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

> **Read this section before your first deploy.** These are real issues we hit during staging -- they'll save you time.

## Gotchas

- **`.env` is not in git.** You must copy it to the VPS separately. The deploy script checks for it and fails with instructions if missing.
- **Docker creates directories for missing file mounts.** If a compose bind mount references a file that doesn't exist (e.g. OJS import XML), Docker creates it as an empty directory. You must `down -v` and `up` again after adding the missing file. The deploy script handles this by syncing non-git files before starting containers.
- **First image build takes ~3 minutes** on a 3 vCPU VPS (compiling PHP extensions). Subsequent builds use Docker cache.
- **OJS base image is amd64 only.** ARM servers won't work (`platform: linux/amd64` in compose).
- **Composer install runs automatically** on first WP setup when WordPress core files are missing (Bedrock downloads WP core + plugins via Composer).
- **Paid plugins must be copied separately** — licensed code can't be in a public git repo. Use `rsync` to sync `wordpress/paid-plugins/` to the VPS before running setup.
- **`scp` creates files with 600 permissions.** Apache/www-data can't read them. The deploy script runs `chmod 644` on `.env` automatically. Do not change this to 600 — WP-CLI (root) will work but the web server won't, and you'll only catch it via the smoke test's admin page check.
- **No default passwords.** `WP_ADMIN_PASSWORD` and `OJS_ADMIN_PASSWORD` must be set in `.env`. Both `docker-compose.yml` and the setup scripts fail loudly if they're missing or empty. The deploy script validates all required env vars before starting containers.
- **Bulk sync is a manual step.** After setup, existing WP members are not automatically synced to OJS. Run `wp ojs-sync sync --bulk --dry-run` to preview, then `wp ojs-sync sync --bulk --yes` to execute (~5-10 min for ~700 members). The `--bulk` flag is required to prevent accidental full sync. This is deliberate — it's a one-time operation that should be reviewed before running.
- **HPOS and sample data.** When loading sample data (`--with-sample-data`), the setup script temporarily disables HPOS (High-Performance Order Storage), seeds subscriptions via raw SQL into `wp_posts`, then syncs to HPOS and re-enables it. Without this, WooCommerce can't see the seeded subscriptions.

---

## Provider notes: Hetzner Cloud (recommended)

We use [Hetzner Cloud](https://www.hetzner.com/cloud/) for staging and production. Good value, EU data centres, straightforward API.

### Recommended plan

**CPX22** — 3 vCPU (AMD), 4 GB RAM, 80 GB SSD. Runs the full stack comfortably with headroom for sync operations and traffic spikes. ~7 EUR/month.

### Quick start with init-vps.sh

`scripts/init-vps.sh` automates the full Hetzner setup: SSH key, server creation, firewall, SSH config, and GitHub deploy key.

```bash
# Requires hcloud CLI and HCLOUD_TOKEN env var
scripts/init-vps.sh --name=my-server

# With SSL ports (production)
scripts/init-vps.sh --name=my-server --ssl

# Custom server type or location
scripts/init-vps.sh --name=my-server --type=cpx32 --location=fsn1
```

### Manual hcloud commands (reference)

If you prefer to set up manually or need to manage existing servers:

```bash
# Upload SSH key
hcloud ssh-key create --name my-key --public-key-from-file ~/.ssh/my-key.pub

# Create server
hcloud server create --name my-server --type cpx22 --image ubuntu-24.04 \
  --location nbg1 --ssh-key my-key

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
