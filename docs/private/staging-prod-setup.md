# Staging & Production Setup

How to stand up the WP-OJS stack on a fresh VPS. Fully scripted — no manual steps after initial SSH key setup.

---

## Prerequisites

- **Hetzner Cloud account** with API token (set as `HCLOUD_TOKEN` env var)
- **SSH key pair** on your local machine (e.g. `~/.ssh/hetzner`)
- **GitHub deploy key** on the VPS (read-only access to the repo)
- **hcloud CLI** installed (included in the devcontainer)

---

## One-time infrastructure setup

Creates the server, firewall, SSH config, and deploy key. Run once per server.

```bash
# Staging
scripts/init-vps.sh --name=sea-staging

# Production (also opens ports 80/443 for SSL)
scripts/init-vps.sh --name=sea-prod --ssl
```

This handles everything: SSH key generation/upload, Hetzner server creation, firewall rules, SSH config entry, GitHub deploy key. All steps are idempotent — safe to re-run.

### What init-vps.sh does

1. Generates `~/.ssh/hetzner` key (if missing) and uploads to Hetzner
2. Creates CPX22 server in Nuremberg (3 vCPU, 4 GB RAM, ~7 EUR/month)
3. Creates firewall with SSH + WP (8080) + OJS (8081) rules
4. Adds SSH config entry for the server name
5. Generates a deploy key on the VPS and registers it with GitHub

### Flags

| Flag | Default | Effect |
|---|---|---|
| `--name=<name>` | (required) | Server name and SSH alias |
| `--type=<type>` | `cpx22` | Hetzner server type |
| `--location=<loc>` | `nbg1` | Hetzner data centre |
| `--ssl` | off | Also open ports 80/443 for Caddy |
| `--skip-server` | off | Skip server creation (already exists) |

### Prerequisites

`init-vps.sh` requires:
- `hcloud` CLI + `HCLOUD_TOKEN` env var (Hetzner API access)
- `gh` CLI authenticated with access to the repo (for deploy key registration)
- SSH key at `~/.ssh/hetzner`

### Who runs what — options

`init-vps.sh` needs both Hetzner and GitHub access. In practice, not everyone has both. Three ways to handle this:

**Option A: Dev runs everything (simplest — they already have repo access)**

They set up the Hetzner account, run `gh auth login`, then `init-vps.sh` works end to end. They own the infrastructure.

```bash
# They run this once per server
scripts/init-vps.sh --name=sea-prod --ssl

# Then deploy
scripts/deploy.sh --host=sea-prod --provision
```

**Option B: You run init, they run deploys**

You have the Hetzner token and GitHub access. Run `init-vps.sh` end to end. Give them SSH access to the resulting server. They just use `deploy.sh` and `smoke-test.sh` going forward.

**Option C: Split — they set up Hetzner, you register the deploy key**

They create the Hetzner account, give you the API token. You run `init-vps.sh`. Or they run it without `gh` CLI — it will warn but continue. Then register the deploy key manually:

```bash
# Get the public key from the VPS
ssh sea-prod "cat /root/.ssh/deploy_key.pub"

# Add to GitHub via UI:
# Repo → Settings → Deploy keys → Add deploy key → paste the public key
```

### Manual step: .env file

The only thing `init-vps.sh` doesn't do is create the `.env` — this has site-specific URLs and passwords that need human input.

```bash
cp .env.example .env.staging
# Edit: set WP_HOME, OJS_BASE_URL, passwords, API keys
scp .env.staging sea-staging:/opt/wp-ojs-sync/.env
```

---

## Deploying the stack

### First deploy (fresh server)

```bash
# 1. Create .env from template (generates random passwords)
#    Review and adjust values — especially URLs and journal metadata.
#    IMPORTANT: WPOJS_API_KEY must match WPOJS_API_KEY_SECRET
#    IMPORTANT: DB_PASSWORD must match WP_DB_PASSWORD
cp .env.example .env.staging
# Edit .env.staging: set WP_HOME, OJS_BASE_URL, passwords, etc.

# 2. Copy .env to server
scp .env.staging sea-staging:/opt/wp-ojs-sync/.env

# 3. Provision + deploy (installs Docker, clones repo, builds, starts, runs setup)
scripts/deploy.sh --provision

# 4. Copy files not in git:
#    - Paid plugins (licensed, can't be in public repo)
rsync -az -e ssh wordpress/paid-plugins/ sea-staging:/opt/wp-ojs-sync/wordpress/paid-plugins/
#    - OJS import XML (62MB, too large for git)
scp "data export/ojs-import-clean.xml" "sea-staging:/opt/wp-ojs-sync/data export/ojs-import-clean.xml"

# 5. Re-run setup (now that paid plugins + XML are present)
ssh sea-staging "cd /opt/wp-ojs-sync && docker compose -f docker-compose.yml -f docker-compose.staging.yml down -v && docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d"
scripts/deploy.sh --skip-build
```

### Subsequent deploys (code updates)

```bash
# Deploy latest main branch
scripts/deploy.sh

# Deploy a specific branch
scripts/deploy.sh --ref=feature-branch

# Quick restart (no image rebuild, no setup)
scripts/deploy.sh --skip-build --skip-setup
```

### Deploy script flags

| Flag | Effect |
|---|---|
| `--provision` | Install Docker on fresh VPS first |
| `--skip-setup` | Don't run setup.sh (just update code + restart) |
| `--skip-build` | Don't rebuild Docker images |
| `--ref=<branch>` | Deploy a specific git ref (default: main) |
| `--clean` | Tear down volumes first (fresh databases) |
| `--host=<name>` | SSH host alias (default: sea-staging) |

---

## Environment file

The `.env` file on the VPS controls all configuration. Key things to get right:

| Variable | What | Gotcha |
|---|---|---|
| `WP_HOME` | Full URL to WP (e.g. `http://159.69.152.19:8080`) | Must include port |
| `OJS_BASE_URL` | Full URL to OJS (e.g. `http://159.69.152.19:8081`) | Must include port |
| `WP_ADMIN_PASSWORD` | WordPress admin password | **Required — no default.** Setup fails if missing. |
| `OJS_ADMIN_PASSWORD` | OJS admin password | **Required — no default.** Setup fails if missing. |
| `WPOJS_API_KEY` | API key WP sends to OJS | Must match `WPOJS_API_KEY_SECRET` |
| `WPOJS_API_KEY_SECRET` | API key OJS validates | Must match `WPOJS_API_KEY` |
| `DB_PASSWORD` | WP DB password (Bedrock reads this) | Must match `WP_DB_PASSWORD` |
| `WP_DB_PASSWORD` | WP DB password (Docker Compose reads this) | Must match `DB_PASSWORD` |
| Auth salts | WP security salts | Generate unique values per environment |

---

## Architecture on VPS

```
/opt/wp-ojs-sync/          ← git repo clone
├── .env                   ← environment config (not in git)
├── docker-compose.yml     ← base compose
├── docker-compose.staging.yml  ← staging overrides (ports 8080/8081)
├── docker-compose.caddy.yml   ← optional SSL overlay
├── scripts/
│   ├── deploy.sh          ← run FROM devcontainer
│   ├── provision-vps.sh   ← run ON VPS (via deploy.sh --provision)
│   └── setup.sh           ← run INSIDE containers (via deploy.sh)
└── ...
```

Docker services:
- `wp` — WordPress (Apache + PHP 8.2) → port 8080
- `wp-db` — MariaDB 10.11
- `ojs` — OJS 3.5 (Apache + PHP) → port 8081
- `ojs-db` — MariaDB 10.11

---

## Adding SSL (production)

When you have a domain:

1. Add DNS A records pointing to the server IP
2. Add to `.env`:
   ```
   CADDY_WP_DOMAIN=wp.yourdomain.org
   CADDY_OJS_DOMAIN=journal.yourdomain.org
   ```
3. Open ports 80/443 in firewall
4. Deploy with Caddy overlay:
   ```bash
   ssh sea-staging "cd /opt/wp-ojs-sync && docker compose -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.caddy.yml up -d"
   ```

Caddy handles Let's Encrypt automatically.

---

## Testing a deployment

### Smoke tests

Lightweight checks (curl + WP-CLI via SSH). No Node or Playwright on the VPS — runs from the devcontainer.

```bash
# Test staging (default)
scripts/smoke-test.sh

# Test a different host
scripts/smoke-test.sh --host=sea-prod
```

Tests (17 checks):
1. WP HTTP responds
1b. WP Admin page loads (catches .env permission issues, PHP fatals)
1c. OJS Admin page loads (catches missing journal, PHP fatals)
2. OJS HTTP responds
3. WP REST API responds
4. OJS plugin ping
5. OJS preflight (auth + compatibility)
6. WP-CLI `test-connection`
7. Required plugins active (5 plugins)
8. OJS subscription types configured
9. Full sync round-trip (create WP user → sync to OJS → verify subscription → cleanup)
10. Reconciliation completes

### Load tests

Performance testing with [`hey`](https://github.com/rakyll/hey). Runs from the devcontainer, monitors server resources (CPU, memory, Docker stats) during the test.

```bash
# Install hey (one-time)
curl -sfL https://hey-release.s3.us-east-2.amazonaws.com/hey_linux_amd64 -o /usr/local/bin/hey && chmod +x /usr/local/bin/hey

# Standard load (50 concurrent, 500 requests per endpoint)
scripts/load-test.sh

# Lighter load (10 concurrent, 100 requests)
scripts/load-test.sh --light

# Test a different host
scripts/load-test.sh --host=sea-prod
```

Scenarios tested (5 endpoints):
1. OJS journal homepage
2. OJS article page
3. OJS API preflight
4. WP homepage
5. WP REST API

Pass criteria: p95 latency ≤ 2000ms, zero server errors. Reports peak CPU load and memory usage during the test.

**Note:** `hey` generates harder-than-real load (no think time, no browser rendering, no connection reuse). If the server passes these tests, it will handle real traffic comfortably.

---

## Useful commands

```bash
# SSH to server
ssh sea-staging

# View logs
ssh sea-staging "cd /opt/wp-ojs-sync && docker compose -f docker-compose.yml -f docker-compose.staging.yml logs -f --tail=50"

# Check container status
ssh sea-staging "cd /opt/wp-ojs-sync && docker compose -f docker-compose.yml -f docker-compose.staging.yml ps"

# Run WP-CLI
ssh sea-staging "cd /opt/wp-ojs-sync && docker compose -f docker-compose.yml -f docker-compose.staging.yml exec wp wp --allow-root ojs-sync test-connection"

# Access OJS DB
ssh sea-staging "cd /opt/wp-ojs-sync && docker compose -f docker-compose.yml -f docker-compose.staging.yml exec ojs-db mysql -u ojs -p ojs"

# Nuke and restart (destroys data!)
ssh sea-staging "cd /opt/wp-ojs-sync && docker compose -f docker-compose.yml -f docker-compose.staging.yml down -v && docker compose -f docker-compose.yml -f docker-compose.staging.yml up -d"
```

---

## Rebuilding a server from scratch

If the VPS is destroyed or you need a fresh start:

```bash
# Recreate server (if destroyed)
hcloud server create --name sea-staging --type cpx22 --image ubuntu-24.04 --location nbg1 --ssh-key hetzner

# Or rebuild existing server
hcloud server rebuild --image ubuntu-24.04 sea-staging

# Re-inject SSH key via cloud-init if rebuild doesn't pick it up
hcloud server rebuild --image ubuntu-24.04 --user-data-from-file <(echo -e "#cloud-config\nssh_authorized_keys:\n  - $(cat ~/.ssh/hetzner.pub)") sea-staging

# Set up deploy key again (rebuild wipes /root/.ssh)
# Then: scp .env.staging, deploy.sh --provision
```

---

## Good to know

- **`hcloud server rebuild` does NOT inject SSH keys** from your Hetzner account. Use `--user-data-from-file` with cloud-init to inject the key, or use `hcloud server create` (which does inject keys).
- **First image build takes ~3 minutes** on CPX22 (compiling PHP extensions). Subsequent builds use cache.
- **OJS base image is amd64 only** (`platform: linux/amd64` in compose). ARM VPS won't work.
- **`.env` is not in git.** You must `scp` it to the VPS separately. The deploy script checks for its existence and fails with instructions if missing.
- **Docker creates directories for missing file mounts.** If a compose bind mount references a file that doesn't exist, Docker creates it as an empty directory. You must `down -v` and `up` again after adding the missing file.
- **Paid plugins must be copied separately** — licensed code, can't be in git. Use `rsync` to copy `wordpress/paid-plugins/` to the VPS.
- **Composer install runs automatically** on first WP setup when `web/wp/wp-includes` is missing (Bedrock downloads WP core + plugins via Composer).
- **No default passwords.** `WP_ADMIN_PASSWORD` and `OJS_ADMIN_PASSWORD` must be set in `.env`. Both `docker-compose.yml` (`:?` syntax) and the setup scripts fail loudly if they're missing. The deploy script validates all required env vars before starting containers.
- **`.env` must be chmod 644, not 600.** `scp` creates 600 by default. The deploy script fixes this automatically. Apache (www-data) must read the file — 600 breaks WP web pages while WP-CLI (root) still works, making it hard to catch without the admin page smoke test.
- **Bulk sync is manual.** Setup does not sync WP members to OJS. After a fresh deploy with sample data, run: `wp ojs-sync sync --bulk --dry-run` then `wp ojs-sync sync --bulk --yes` (~5-10 min for ~700 members).
- **HPOS and sample data seeding.** The setup script temporarily disables HPOS before seeding sample subscriptions (raw SQL into `wp_posts`), then syncs to HPOS and re-enables it. Without this, WooCommerce queries the empty HPOS table and sees 0 subscriptions.
