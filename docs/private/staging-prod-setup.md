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

These steps create the server and configure access. Only needed once.

### 1. Create SSH key and upload to Hetzner

```bash
# Generate key (no passphrase for automation)
ssh-keygen -t ed25519 -f ~/.ssh/hetzner -N "" -C "hetzner"

# Upload to Hetzner
hcloud ssh-key create --name hetzner --public-key-from-file ~/.ssh/hetzner.pub
```

### 2. Create server

```bash
hcloud server create \
  --name sea-staging \
  --type cpx22 \
  --image ubuntu-24.04 \
  --location nbg1 \
  --ssh-key hetzner
```

### 3. Create firewall

```bash
hcloud firewall create --name sea-staging-fw
hcloud firewall add-rule sea-staging-fw --direction in --protocol tcp --port 22 --source-ips 0.0.0.0/0 --source-ips ::/0 --description SSH
hcloud firewall add-rule sea-staging-fw --direction in --protocol tcp --port 8080 --source-ips 0.0.0.0/0 --source-ips ::/0 --description WP
hcloud firewall add-rule sea-staging-fw --direction in --protocol tcp --port 8081 --source-ips 0.0.0.0/0 --source-ips ::/0 --description OJS
hcloud firewall apply-to-resource sea-staging-fw --type server --server sea-staging
```

For production, also open ports 80 and 443 (Caddy SSL).

### 4. Configure SSH

Add to `~/.ssh/config`:

```
Host sea-staging
  HostName <server-ip>
  User root
  IdentityFile ~/.ssh/hetzner
  IdentitiesOnly yes
```

Verify: `ssh sea-staging hostname`

### 5. Set up GitHub deploy key on VPS

```bash
# Generate deploy key on VPS
ssh sea-staging "ssh-keygen -t ed25519 -f /root/.ssh/deploy_key -N '' -C 'wp-ojs-sync-deploy'"

# Get the public key
ssh sea-staging "cat /root/.ssh/deploy_key.pub"

# Add to GitHub (from devcontainer with gh CLI)
ssh sea-staging "cat /root/.ssh/deploy_key.pub" | gh repo deploy-key add - --repo Pharkie/wp-ojs-sync --title "sea-staging-vps"

# Configure VPS to use deploy key for GitHub
ssh sea-staging 'cat > /root/.ssh/config << EOF
Host github.com
  IdentityFile /root/.ssh/deploy_key
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
chmod 600 /root/.ssh/config'

# Verify
ssh sea-staging "ssh -T git@github.com"
# Should say: "Hi Pharkie/wp-ojs-sync! You've successfully authenticated..."
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
| `--host=<name>` | SSH host alias (default: sea-staging) |

---

## Environment file

The `.env` file on the VPS controls all configuration. Key things to get right:

| Variable | What | Gotcha |
|---|---|---|
| `WP_HOME` | Full URL to WP (e.g. `http://159.69.152.19:8080`) | Must include port |
| `OJS_BASE_URL` | Full URL to OJS (e.g. `http://159.69.152.19:8081`) | Must include port |
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

## Gotchas

- **`hcloud server rebuild` does NOT inject SSH keys** from your Hetzner account. Use `--user-data-from-file` with cloud-init to inject the key, or use `hcloud server create` (which does inject keys).
- **First image build takes ~3 minutes** on CPX22 (compiling PHP extensions). Subsequent builds use cache.
- **OJS base image is amd64 only** (`platform: linux/amd64` in compose). ARM VPS won't work.
- **`.env` is not in git.** You must `scp` it to the VPS separately. The deploy script checks for its existence and fails with instructions if missing.
- **Docker creates directories for missing file mounts.** If a compose bind mount references a file that doesn't exist, Docker creates it as an empty directory. You must `down -v` and `up` again after adding the missing file.
- **Paid plugins must be copied separately** — licensed code, can't be in git. Use `rsync` to copy `wordpress/paid-plugins/` to the VPS.
- **Composer install runs automatically** on first WP setup when `web/wp/wp-includes` is missing (Bedrock downloads WP core + plugins via Composer).
