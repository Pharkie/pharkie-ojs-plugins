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
- **OJS 3.5 upgrade is the biggest risk.** The 3.5 upgrade has significant breaking changes (Slim->Laravel, Vue 2->3). If this goes badly, re-evaluate Janeway migration.

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
- **`scripts/setup.sh`** — unified setup for all environments. Assumes containers are already running. Flags: `--env=dev|staging|prod`, `--with-sample-data`. Staging defaults to `--with-sample-data`.
- **`scripts/setup-dev.sh`** — thin shim, runs `setup.sh --env=dev`. Kept for backwards compatibility.
- **Why two scripts?** Docker-in-Docker in the devcontainer requires a hardcoded host path for `--project-directory`. `rebuild-dev.sh` bakes this in. `setup.sh --env=dev` is the portable inner script. Staging/prod use plain `docker compose` on the VPS.
- **`scripts/init-vps.sh`** — one-time VPS setup (Hetzner): creates server, firewall, SSH config. Run once per server.
- **`scripts/deploy.sh`** — deploys code to a VPS via SSH: git pull, build images, start containers, run setup. Run every time you ship code. Flags: `--host`, `--provision`, `--skip-setup`, `--skip-build`, `--ref`, `--clean`, `--env-file`.
- **`scripts/smoke-test.sh`** — lightweight staging/prod health checks via SSH (curl + WP-CLI). No Node/Playwright needed on VPS.
- **`scripts/load-test.sh`** — performance tests using `hey` with server resource monitoring.
- **Post-rebuild prompt:** `docs/private/claude-dev-setup-prompt.md` — copy-paste prompt for a fresh Claude session after devcontainer rebuild.

## Backfill pipeline

Imports ~30 years of journal back-issues (whole-issue PDFs) into OJS. Two scripts, two steps:

1. **`backfill/split-issue.sh <issue.pdf>`** — split a whole-issue PDF into per-article PDFs + OJS Native XML. Local only, does not touch OJS. Output: `backfill/output/vol##-iss#/` (toc.json, per-article PDFs, import.xml).
2. **`backfill/import.sh <issue-dir>`** — load the split output into OJS via Docker CLI.

Individual Python scripts (called by `split-issue.sh` in order):
- `backfill/preflight.py` — validate PDF, detect vol/issue
- `backfill/parse_toc.py` — parse CONTENTS page, extract metadata (titles, authors, abstracts, keywords, page ranges)
- `backfill/split.py` — split PDF into per-article PDFs using PyMuPDF
- `backfill/author_normalize.py` — normalize author names against registry (`backfill/authors.json`)
- `backfill/generate_xml.py` — generate OJS Native XML with base64-embedded PDFs
- `backfill/verify.py` — post-import verification against OJS database

Output directory (`backfill/output/`) is gitignored (large PDFs + XML).

## Pre-commit hooks

Installed via `./setup-hooks.sh` (runs automatically in dev container). Symlinks `.git/hooks/pre-commit` to `scripts/pre-commit`. Checks: secret detection, env var documentation, YAML syntax, doc link validation. Modular checks live in `scripts/lib/`.

## Don't

- Modify OJS source code
- Sync plaintext passwords between systems (password hashes are synced during bulk sync — this is safe)
- Build message queues, webhook servers, or microservices
- Add features beyond the core sync requirement
- Assume any OJS API endpoint exists without checking `docs/ojs-sync-plugin-api.md`
- Revisit OIDC SSO or Pull-verify — both eliminated with documented reasons
