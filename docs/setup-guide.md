# Setup Guide

Dev environment setup, secrets management, and devcontainer details. For Docker container setup, see [`docker-setup.md`](docker-setup.md). For VPS deployment, see [`vps-deployment.md`](vps-deployment.md).

## Dev environment scripts

- **`scripts/dev/rebuild-dev.sh`** — full grave-and-pave: tears down containers+volumes, rebuilds images, brings up stack, runs setup, runs tests. Devcontainer-only (hardcoded host path for DinD volume mounts). Flags: `--with-sample-data`, `--skip-tests`.
  - **For full dev environment with all content:** run `rebuild-dev.sh --with-sample-data --skip-tests` (seeds ~1400 test WP users + subscriptions), then `sudo bash backfill/html_pipeline/pipe7_import.sh backfill/private/output/* --wipe-articles` (imports all issues with HTML + PDF galleys). The `--wipe-articles` flag wipes existing issues/articles first (users/subscriptions/payments kept).
  - **For quick dev cycle:** `rebuild-dev.sh --with-sample-data` gives 2 sample issues + test users — enough for sync testing without the backfill wait.
- **`scripts/infra/setup.sh`** — unified setup for all environments. Assumes containers are already running. Flags: `--env=dev|staging|prod`, `--with-sample-data`. Sample data is always opt-in (never auto-included).
- **`scripts/infra/setup-dev.sh`** — thin shim, runs `setup.sh --env=dev`. Kept for backwards compatibility.

### Why two scripts?

Docker-in-Docker in the devcontainer requires the host path for `--project-directory` (volume mounts resolve against the host filesystem). `rebuild-dev.sh` is the outer script (tear down + build + setup). `setup.sh --env=dev` is the portable inner script. Staging/prod use plain `docker compose` on the VPS.

### DinD abstraction

`scripts/lib/dc.sh` provides `init_dc` which auto-detects DinD via `HOST_PROJECT_DIR` env var (set in `devcontainer.json` from `${localWorkspaceFolder}`). All scripts source it and use `$DC`. No hardcoded host paths anywhere.

## Deployment scripts

- **`scripts/infra/init-vps.sh`** — one-time VPS setup (Hetzner): creates server, firewall, SSH config. Run once per server.
- **`scripts/infra/deploy.sh`** — deploys code to a VPS via SSH: git pull, build images, start containers, run setup. Run every time you ship code. Flags: `--host`, `--provision`, `--skip-setup`, `--skip-build`, `--ref`, `--clean`, `--env-file`.
- **`scripts/monitoring/smoke-test.sh`** — lightweight staging/prod health checks via SSH (curl + WP-CLI). Includes backup health checks.
- **`scripts/monitoring/load-test.sh`** — performance tests using `hey` with server resource monitoring.
- **`scripts/ojs/backup-ojs-db.sh`** — runs ON the VPS (via cron at 03:00 UTC). Dumps OJS DB → gzip → AES-256-CBC encrypt → rotate (7 daily + 4 weekly).
- **`scripts/infra/pull-ojs-backup.sh`** — runs FROM devcontainer. Pull, list, decrypt backups. Also manages VPS cron (`--install-cron`, `--remove-cron`). Off-server storage via GitHub Actions → a private backup repo (daily schedule).

## Secrets management

Private docs and env files live in a **separate private GitHub repo**, cloned into `private/` (which is gitignored in the public repo). The devcontainer `postCreateCommand` auto-clones it on rebuild.

### What's in the private repo

- Markdown docs — plans, setup guides, review findings, checklists (unencrypted, no secrets)
- **`.env.live`** and **`.env.staging`** — SOPS-encrypted (contain all production/staging secrets: DB passwords, API keys, Stripe keys, SMTP creds)
- **`.sops.yaml`** — SOPS config with the age public key
- **`editorial-roles.json`** — OJS editorial team mapping

### How SOPS encryption works

`.env.live` and `.env.staging` are encrypted with [SOPS](https://github.com/getsops/sops) using [age](https://github.com/FiloSottile/age) as the backend. The files are JSON on disk (not plaintext key=value). You cannot `cat` them and read values — you must use `sops` to decrypt.

- **Encryption key**: age keypair. Public key in `private/.sops.yaml`. Private key at `~/.config/sops/age/keys.txt` (bind-mounted from host into devcontainer).
- **SOPS auto-discovers the age key** from `~/.config/sops/age/keys.txt` — no env vars needed.
- **If the age key is missing**, sops commands will fail with `could not decrypt`. The key must exist on the host machine at `~/.config/sops/age/` before the devcontainer is built.

### Common operations

```bash
# Read a value from encrypted env file
sops -d private/.env.live | grep OJS_ADMIN_PASSWORD

# Edit secrets interactively (decrypts in $EDITOR, re-encrypts on save)
sops private/.env.live

# Add/update a secret programmatically (4 steps):
#   1. Decrypt to temp file
sops -d private/.env.live > /tmp/.env.live.plain
#   2. Edit the plaintext (append, sed, etc.)
echo 'NEW_VAR=value' >> /tmp/.env.live.plain
#   3. Re-encrypt (input file MUST match .sops.yaml path_regex: .env.live or .env.staging)
cp /tmp/.env.live.plain /tmp/.env.live
sops -e --config private/.sops.yaml --input-type binary --output-type json /tmp/.env.live > /tmp/.env.live.enc
cp /tmp/.env.live.enc private/.env.live
#   4. Verify and clean up
sops -d private/.env.live | grep NEW_VAR
rm /tmp/.env.live*

# Deploy (deploy.sh auto-detects SOPS and decrypts before SCP)
scripts/infra/deploy.sh --host=<your-server> --ssl --env-file=.env.live

# Decrypt to a temp file (for manual inspection)
sops -d private/.env.live > /tmp/.env.live
# ... inspect ...
rm /tmp/.env.live

# Commit changes to the private repo (it's a separate git repo!)
cd private && git add -A && git commit -m "Update env" && git push
```

### Important: two git repos

`private/` is its own git repo (cloned from your private GitHub repo). It is NOT part of the public repo. To commit changes to private docs or env files:

```bash
cd private
git add -A && git commit -m "description" && git push
cd /workspaces/pharkie-ojs-plugins  # back to public repo
```

Running `git add` from the public repo root will NOT stage anything inside `private/` — it's gitignored and has its own `.git/`.

### Symlinks

`.env.live` and `.env.staging` at the repo root are **symlinks** to `private/.env.live` and `private/.env.staging`. This lets `deploy.sh --env-file=.env.live` work without knowing about the private repo. The symlinks point to encrypted files — deploy.sh detects SOPS format and auto-decrypts.

### First-time setup (new machine / fresh devcontainer)

If `private/` is missing or `~/.config/sops/age/keys.txt` doesn't exist:

```bash
# 1. Clone private repo (if not auto-cloned by postCreateCommand)
gh repo clone <your-org>/<private-repo> private

# 2. Create symlinks
ln -sf private/.env.live .env.live
ln -sf private/.env.staging .env.staging

# 3. Age key — get from password manager, save to:
mkdir -p ~/.config/sops/age
# Paste the AGE-SECRET-KEY-... line into:
#   ~/.config/sops/age/keys.txt

# 4. Verify
sops -d private/.env.live | head -3
```
