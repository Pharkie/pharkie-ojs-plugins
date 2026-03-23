# CLAUDE.md

## Project

WordPress ↔ OJS integration. WP manages memberships via WooCommerce Subscriptions; OJS hosts a journal behind a paywall. Goal: members get access automatically, non-members can still buy content.

## Key docs (read these first)

- `docs/private/plan.md` — implementation plan: what we're building, how it works, endpoint specs, launch sequence, testing approach
- `docs/discovery.md` — decision trail: what was tried, what was eliminated, and why
- `docs/private/review-findings.md` — multi-perspective plan review and how findings were resolved
- `docs/ojs-sync-plugin-api.md` — OJS plugin REST API reference (endpoints, auth, errors)
- `docs/ojs-internals.md` — OJS native API, DB schema, PHP internals (research notes)
- `docs/wp-integration.md` — WP membership stack (Ultimate Member + WooCommerce Subscriptions), hooks, code patterns
- `docs/private/janeway-paywall-investigation.md` — concrete technical plan for Janeway backup path
- `docs/non-docker-setup.md` — non-Docker setup: plugin installation, config, troubleshooting
- `docs/private/hosting-requirements.md` — OJS + WP hosting specs and access requirements for staging/production
- `docs/support-runbook.md` — support staff quick reference for common member issues
- `docs/private/membership-platform.md` — membership platform comparison (WildApricot, CiviCRM, Beacon, Outseta)
- `TODO.md` — roadmap with phased implementation steps

## Architecture decision

**Push-sync** (custom OJS plugin + WP plugin). A plugin on each side: the OJS plugin exposes REST endpoints for user and subscription CRUD (OJS has no native subscription API). The WP plugin calls those endpoints. Two modes of operation:

1. **Initial bulk sync:** WP-CLI command reads all active WooCommerce Subscriptions, creates OJS user accounts (with WP password hashes) and subscription records for each member via the OJS plugin endpoints. Members can immediately log into OJS with their existing WP password — no separate "set your password" step needed. OJS custom hasher verifies WP hashes at login and lazy-rehashes to native bcrypt.
2. **Ongoing sync (after launch):** WP plugin hooks into WooCommerce Subscription lifecycle events (active, expired, cancelled, on-hold) and pushes changes to OJS automatically via an async queue.

See `docs/private/plan.md` for full details, `docs/discovery.md` for how we got here.

**Janeway migration** is a genuine backup (not a nuclear option) if the OJS 3.5 upgrade proves too costly. See `docs/discovery.md` for the comparison.

## Plan naming

| Name | What | Status |
|---|---|---|
| **OIDC SSO** | OpenID Connect SSO | Eliminated |
| **Pull-verify** | Subscription SSO plugin (OJS asks WP at access time) | Eliminated |
| **Push-sync** | WP pushes to OJS via plugins on each side | **Chosen** |
| **Push-sync (direct DB)** | Same but writes to OJS DB directly | Fallback |
| **XML user import** | OJS built-in XML import (users only, not subscriptions) | Eliminated |
| **Janeway migration** | Replace OJS with Janeway + custom paywall | Genuine backup |

## Hard constraints

- **WP is source of truth** for membership. OJS is downstream.
- **Email is the matching key.** Same email required on both systems. No separate mapping table. Members who want a different email on OJS must update their WP email first.
- **Bulk sync creates OJS accounts.** Don't wait for members to self-register. Push user accounts + subscriptions from WP upfront (existing members at launch).
- **OJS paywall must keep working** for non-member purchases (article, issue, back issue).
- **No OJS core modifications.** Plugins only.
- **Ship fast.** Prefer boring, reliable solutions.

## Eliminated approaches (don't revisit)

- **OIDC SSO** — only solves login not access; OJS plugin has unresolved bugs, no 3.5 release, breaks multi-journal.
- **Pull-verify** (Subscription SSO plugin) — source code audit confirmed it hijacks OJS purchase flow. Non-members can't buy content. See `docs/phase0-sso-plugin-audit.md`.
- **Native REST API sync** — subscription endpoints don't exist in any OJS version. Push-sync works around this with a custom OJS plugin.
- **XML user import** — creates user accounts only, not subscriptions. Roles that bypass the paywall are editorial/admin (inappropriate for members, no expiry control). No "subscriber" role exists. See `docs/xml-import-evaluation.md`.

## Good to know

- **OJS has NO subscription REST API.** The endpoints don't exist. That's why we need a custom OJS plugin. See `docs/ojs-sync-plugin-api.md`.
- **OJS plugin uses `getInstallMigration()`**, not `getInstallSchemaFile()` (which is `final` in OJS 3.5). See `WpojsApiLogMigration.php`.
- **OJS plugin folder must be `wpojsSubscriptionApi`** (camelCase). Hyphens/underscores break autoloading and the Plugins admin page. See `docs/non-docker-setup.md`.
- **Apache + PHP-FPM strips Authorization headers.** Need `CGIPassAuth on` in `.htaccess`. Do not use `?apiToken=` query param in production (leaks key into access logs).
- **OJS 3.5 stores galley labels in `publication_galleys.label` column**, not in `publication_galley_settings`. Inserting label rows into the settings table causes `getLabel()` to return a localized array instead of a string, breaking label comparisons (e.g. inline HTML plugin's `=== 'Full Text'` check).
- **htmlgen can drop repeated/multilingual content.** Haiku may treat transliterated references (e.g. Cyrillic then Latin script) as duplicates and omit one set. The prompt now explicitly says to include both, but always verify HTML galleys against source PDFs for articles with non-English references.
- **OJS 3.5 upgrade is the biggest risk.** The 3.5 upgrade has significant breaking changes (Slim->Laravel, Vue 2->3). If this goes badly, re-evaluate Janeway migration.
- **WP usernames are synced to OJS** but sanitized to lowercase-alphanumeric (OJS constraint). 38% of live WP usernames contain dots, hyphens, underscores, spaces, or `@` (mostly email-as-username accounts) — these get stripped, so typing the WP login into OJS won't match. Mitigated: the login page relabels the field to "Email" and sets `autocomplete="email"`. OJS login auto-detects email-shaped input and does email lookup. See `docs/ojs-sync-plugin-api.md#username-sync`.

## Custom OJS plugins

Three custom plugins, all bind-mounted into the OJS container via `docker-compose.yml`:

| Plugin | Directory | Mount path | Purpose |
|---|---|---|---|
| **WP-OJS Subscription API** | `plugins/wpojs-subscription-api` | `plugins/generic/wpojsSubscriptionApi` | REST endpoints for user + subscription CRUD (OJS has none natively). Also adds login hint, paywall hint, footer messages. See `docs/ojs-sync-plugin-api.md`. |
| **Inline HTML Galley** | `plugins/ojs-inline-html-galley` | `plugins/generic/inlineHtmlGalley` | Inlines HTML galley content on the article page (instead of download link). Shows subscriber/purchase access messages. |
| **Stripe Payment** | `plugins/stripe-payment` | `plugins/paymethod/stripe` | Stripe Checkout for non-member article/issue purchases. Redirect flow + webhook handler. Replaces PayPal (sandbox broken for UK accounts). |

- **Stripe plugin** uses Stripe Checkout (redirect flow): buyer clicks purchase → OJS creates Checkout Session → redirects to Stripe → payment → redirects back → access granted. Webhook endpoint at `/payment/plugin/StripePayment/webhook` for async confirmation. Uses a restricted API key scoped to Checkout Sessions only.
- **Vendor deps**: `plugins/stripe-payment/vendor/` is gitignored. Stripe PHP SDK is installed via multi-stage Docker build (composer stage) and copied into the bind-mounted plugin dir by the entrypoint.
- **Payment plugin priority** in `setup-ojs.sh`: Stripe (if `OJS_STRIPE_SECRET_KEY`) → Manual Payment. PayPal eliminated (sandbox broken for UK accounts, support unhelpful).
- **Test scripts**: `scripts/test-stripe.js` — standalone Stripe payment test, no OJS involved.

## WP membership stack

**Ultimate Member + WooCommerce + WooCommerce Subscriptions.** UM handles registration/profiles/roles. WCS handles billing. Membership = WP role.

Primary integration: hook into **WooCommerce Subscriptions** status events (`woocommerce_subscription_status_active`, `_expired`, `_cancelled`, `_on-hold`). All sync calls are async (queued via Action Scheduler, not inline). Daily reconciliation catches any drift. See `docs/wp-integration.md` for WCS hook details and `docs/private/plan.md` for the full WP plugin spec.

## Code conventions

- WordPress plugin standards (PHP)
- Prefix everything `wpojs_`
- Use WP HTTP API (`wp_remote_post` etc.) — not raw cURL
- Use Action Scheduler for async jobs and retries
- Log all sync operations — failures must be visible in WP admin
- API key stored as `wp-config.php` constant (`WPOJS_API_KEY`), not in the database
- Settings page for OJS URL, subscription type mapping (WooCommerce Product -> OJS Subscription Type), journal ID(s)
- **No raw SQL in plugin code.** Plugins use their respective frameworks (WordPress HTTP API, OJS DAOs/services, REST endpoints). Direct DB queries are only acceptable in setup/migration scripts (dev environment bootstrapping), never in runtime plugin code.
- **Setup scripts are infrastructure automation** — they bootstrap dev/staging environments with direct DB calls where APIs don't exist (OJS subscription types, plugin settings). This is acceptable because they run once, not on every request.

## Dev environment

- **`scripts/rebuild-dev.sh`** — full grave-and-pave: tears down containers+volumes, rebuilds images, brings up stack, runs setup, runs tests. Devcontainer-only (hardcoded host path for DinD volume mounts). Flags: `--with-sample-data`, `--skip-tests`.
  - **For full dev environment with all content:** run `rebuild-dev.sh --with-sample-data --skip-tests` (seeds ~1400 test WP users + subscriptions), then `backfill/import.sh backfill/output/* --clean` (imports all 68 issues with HTML + PDF galleys, ~10 min, 469MB XML). The `--clean` flag wipes the 2 sample OJS issues first so backfill starts from a clean slate. WP test users are kept.
  - **For quick dev cycle:** `rebuild-dev.sh --with-sample-data` gives 2 sample issues + test users — enough for sync testing without the 10-min backfill wait.
- **`scripts/setup.sh`** — unified setup for all environments. Assumes containers are already running. Flags: `--env=dev|staging|prod`, `--with-sample-data`. Sample data is always opt-in (never auto-included).
- **`scripts/setup-dev.sh`** — thin shim, runs `setup.sh --env=dev`. Kept for backwards compatibility.
- **Why two scripts?** Docker-in-Docker in the devcontainer requires the host path for `--project-directory` (volume mounts resolve against the host filesystem). `rebuild-dev.sh` is the outer script (tear down + build + setup). `setup.sh --env=dev` is the portable inner script. Staging/prod use plain `docker compose` on the VPS.
- **DinD abstraction:** `scripts/lib/dc.sh` provides `init_dc` which auto-detects DinD via `HOST_PROJECT_DIR` env var (set in `devcontainer.json` from `${localWorkspaceFolder}`). All scripts source it and use `$DC`. No hardcoded host paths anywhere.
- **`scripts/init-vps.sh`** — one-time VPS setup (Hetzner): creates server, firewall, SSH config. Run once per server.
- **`scripts/deploy.sh`** — deploys code to a VPS via SSH: git pull, build images, start containers, run setup. Run every time you ship code. Flags: `--host`, `--provision`, `--skip-setup`, `--skip-build`, `--ref`, `--clean`, `--env-file`.
- **`scripts/smoke-test.sh`** — lightweight staging/prod health checks via SSH (curl + WP-CLI). No Node/Playwright needed on VPS. Includes backup health checks (cron, encryption key, latest backup age/size).
- **`scripts/load-test.sh`** — performance tests using `hey` with server resource monitoring.
- **`scripts/backup-ojs-db.sh`** — runs ON the VPS (via cron at 03:00 UTC). Dumps OJS DB → gzip → AES-256-CBC encrypt → rotate (7 daily + 4 weekly). Encryption key at `/opt/backups/ojs/.backup-key`.
- **`scripts/pull-ojs-backup.sh`** — runs FROM devcontainer. Pull, list, decrypt backups. Also manages VPS cron (`--install-cron`, `--remove-cron`). Off-server storage via GitHub Actions → `Pharkie/sea-ojs-db-backups` (private repo, daily at 04:00 UTC).
- **Post-rebuild prompt:** `docs/private/claude-dev-setup-prompt.md` — copy-paste prompt for a fresh Claude session after devcontainer rebuild.

## Secrets management

Private docs and env files live in a **separate private GitHub repo** (`Pharkie/ojs-sea-private`), cloned into `docs/private/` (which is gitignored in the public repo). The devcontainer `postCreateCommand` auto-clones it on rebuild.

### What's in the private repo

- **16 markdown docs** — plans, setup guides, review findings, checklists (unencrypted, no secrets)
- **`.env.live`** and **`.env.staging`** — SOPS-encrypted (contain all production/staging secrets: DB passwords, API keys, Stripe keys, SMTP creds)
- **`.sops.yaml`** — SOPS config with the age public key
- **`editorial-roles.json`** — OJS editorial team mapping

### How SOPS encryption works

`.env.live` and `.env.staging` are encrypted with [SOPS](https://github.com/getsops/sops) using [age](https://github.com/FiloSottile/age) as the backend. The files are JSON on disk (not plaintext key=value). You cannot `cat` them and read values — you must use `sops` to decrypt.

- **Encryption key**: age keypair. Public key in `docs/private/.sops.yaml`. Private key at `~/.config/sops/age/keys.txt` (bind-mounted from host into devcontainer).
- **SOPS auto-discovers the age key** from `~/.config/sops/age/keys.txt` — no env vars needed.
- **If the age key is missing**, sops commands will fail with `could not decrypt`. The key must exist on the host machine at `~/.config/sops/age/` before the devcontainer is built.

### Common operations

```bash
# Read a value from encrypted env file
sops -d docs/private/.env.live | grep OJS_ADMIN_PASSWORD

# Edit secrets (decrypts in $EDITOR, re-encrypts on save)
sops docs/private/.env.live

# Deploy to live (deploy.sh auto-detects SOPS and decrypts before SCP)
scripts/deploy.sh --host=sea-live --ssl --env-file=.env.live

# Decrypt to a temp file (for manual inspection)
sops -d docs/private/.env.live > /tmp/.env.live
# ... inspect ...
rm /tmp/.env.live

# Commit changes to the private repo (it's a separate git repo!)
cd docs/private && git add -A && git commit -m "Update env" && git push
```

### Important: two git repos

`docs/private/` is its own git repo (cloned from `Pharkie/ojs-sea-private`). It is NOT part of the public repo. To commit changes to private docs or env files:

```bash
cd docs/private
git add -A && git commit -m "description" && git push
cd /workspaces/wp-ojs-sync  # back to public repo
```

Running `git add` from the public repo root will NOT stage anything inside `docs/private/` — it's gitignored and has its own `.git/`.

### Symlinks

`.env.live` and `.env.staging` at the repo root are **symlinks** to `docs/private/.env.live` and `docs/private/.env.staging`. This lets `deploy.sh --env-file=.env.live` work without knowing about the private repo. The symlinks point to encrypted files — deploy.sh detects SOPS format and auto-decrypts.

### First-time setup (new machine / fresh devcontainer)

If `docs/private/` is missing or `~/.config/sops/age/keys.txt` doesn't exist:

```bash
# 1. Clone private repo (if not auto-cloned by postCreateCommand)
gh repo clone Pharkie/ojs-sea-private docs/private

# 2. Create symlinks
ln -sf docs/private/.env.live .env.live
ln -sf docs/private/.env.staging .env.staging

# 3. Age key — get from password manager, save to:
mkdir -p ~/.config/sops/age
# Paste the AGE-SECRET-KEY-... line into:
#   ~/.config/sops/age/keys.txt

# 4. Verify
sops -d docs/private/.env.live | head -3
```

## Backfill pipeline

Imports ~30 years of journal back-issues (whole-issue PDFs) into OJS. Three steps:

1. **Create `toc.json`** — Claude reads the PDF and writes `backfill/output/<vol>.<iss>/toc.json` with article metadata. See `docs/backfill-toc-guide.md` for schema and instructions.
2. **`backfill/split-issue.sh <issue.pdf>`** — split the PDF into per-article PDFs + OJS Native XML. Requires toc.json to already exist. Output: `backfill/output/<vol>.<iss>/`.
3. **`backfill/import.sh <issue-dir>`** — load the split output into OJS via Docker CLI.

Pipeline scripts (called by `split-issue.sh`):
- `backfill/preflight.py` — validate PDF, detect vol/issue
- `backfill/split.py` — split PDF into per-article PDFs using PyMuPDF
- `backfill/author_normalize.py` — normalize author names
- `backfill/enrich.py` — enrich toc.json with subjects, disciplines, citations from spreadsheet data
- `backfill/generate_xml.py` — generate OJS Native XML with base64-embedded PDFs + HTML galleys
- `backfill/verify.py` — post-import verification against OJS database

HTML galley generation (step 2.5, between split and import):
- `backfill/htmlgen.py` — sends split PDFs to Claude Haiku API, generates HTML body content for each article. Output: `{split_pdf_stem}.html` next to each split PDF. Resumable (skips existing `.html`). Content-filtered articles get PyMuPDF fallback (marked with `<!-- AUTO-EXTRACTED -->` comment). Report: `backfill/output/htmlgen-report.json`.
- `generate_xml.py` reads `.html` files via `load_html_galley()` — if present, wraps in DOCTYPE/body and embeds as "Full Text" galley. No `.html` = no HTML galley (PDF only).

Standalone utilities:
- `backfill/audit.py` — audit all source PDFs in `backfill/input/` for completeness
- `backfill/compare_archive.py` — compare PDF sources (input/, live WP securepdfs/, etc.)
- `backfill/export_review.py` — export toc.json entries to spreadsheet-compatible format
- `backfill/import_review.py` — import reviewed/corrected spreadsheet data back into toc.json
- `backfill/sheets_export.py` — publish all toc.json data to Google Sheet for review

All 68 existing issues have toc.json files in `backfill/output/`. Large binaries (PDFs, XML) are gitignored. HTML galleys and `htmlgen-report.json` are tracked in git.

### Fixing a bad split or HTML galley

1. Fix `pdf_page_start`/`pdf_page_end` in `backfill/output/<vol>.<iss>/toc.json`
2. Re-split: `backfill/split-issue.sh backfill/input/<vol>.<iss>.pdf`
3. Delete the affected `.html` file(s): `rm backfill/output/<vol>.<iss>/<seq>-<slug>.html`
4. Re-generate HTML: `python3 backfill/htmlgen.py backfill/output/<vol>.<iss>/toc.json --yes`
   (skips all existing `.html`, only regenerates deleted ones — costs pennies)
5. Re-generate XML: `python3 backfill/generate_xml.py backfill/output/<vol>.<iss>/toc.json -o backfill/output/<vol>.<iss>/import.xml`
6. Re-import: `backfill/import.sh backfill/output/<vol>.<iss> --force`

### Fixing an HTML galley on live

**Never re-import on live** — it risks duplicates and ID changes. Instead, update the galley file in place:

1. Edit the `.html` file in `backfill/output/<vol>.<iss>/` and commit
2. Find the galley file path on live:
   ```
   docker compose exec -T ojs-db bash -c 'mysql -u root -p$MYSQL_ROOT_PASSWORD $MYSQL_DATABASE -e "
     SELECT f.path FROM files f
     JOIN submission_files sf ON f.file_id = sf.file_id
     JOIN publication_galleys g ON g.submission_file_id = sf.submission_file_id
     JOIN publications p ON g.publication_id = p.publication_id
     JOIN publication_settings ps ON p.publication_id = ps.publication_id AND ps.setting_name = \"title\"
     WHERE ps.setting_value LIKE \"%Article Title%\" AND f.mimetype = \"text/html\";
   "'
   ```
3. Build the full HTML file (repo files are body-only, live files need the DOCTYPE wrapper):
   ```
   { echo '<!DOCTYPE html>'; echo '<html lang="en">'; echo '<head><meta charset="utf-8"><title>Full Text</title></head>'; echo '<body>'; cat backfill/output/<vol>.<iss>/<seq>-<slug>.html; echo '</body>'; echo '</html>'; } > /tmp/galley-update.html
   ```
4. Copy into the live OJS container:
   ```
   scp /tmp/galley-update.html root@$SERVER_IP:/tmp/
   ssh root@$SERVER_IP "cd /opt/wp-ojs-sync && docker compose cp /tmp/galley-update.html ojs:/var/www/files/<path-from-step-2>"
   ```

## Pre-commit hooks

Installed via `./setup-hooks.sh` (runs automatically in dev container). Symlinks `.git/hooks/pre-commit` to `scripts/pre-commit`. Checks: secret detection, env var documentation, YAML syntax, doc link validation. Modular checks live in `scripts/lib/`.

## Don't

- Modify OJS source code
- Sync plaintext passwords between systems (password hashes are synced during bulk sync — this is safe)
- Build message queues, webhook servers, or microservices
- Add features beyond the core sync requirement
- Assume any OJS API endpoint exists without checking `docs/ojs-sync-plugin-api.md`
- Revisit OIDC SSO or Pull-verify — both eliminated with documented reasons
