# Architecture

WordPress ↔ OJS integration. WP manages memberships via WooCommerce Subscriptions; OJS hosts a journal behind a paywall. Goal: members get access automatically, non-members can still buy content.

## How it works: Push-sync

A plugin on each side. The OJS plugin exposes REST endpoints for user and subscription CRUD (OJS has no native subscription API). The WP plugin calls those endpoints.

1. **Initial bulk sync:** WP-CLI command reads all active WooCommerce Subscriptions, creates OJS user accounts (with WP password hashes) and subscription records via the OJS plugin endpoints. Members can immediately log into OJS with their existing WP password — no separate "set your password" step needed. OJS custom hasher verifies WP hashes at login and lazy-rehashes to native bcrypt.
2. **Ongoing sync (after launch):** WP plugin hooks into WooCommerce Subscription lifecycle events (active, expired, cancelled, on-hold) and pushes changes to OJS automatically via an async queue.

See [`docs/discovery.md`](docs/discovery.md) for the decision trail: what approaches were evaluated and why most were eliminated.

## Approaches evaluated

| Name | What | Status |
|---|---|---|
| **OIDC SSO** | OpenID Connect SSO | Eliminated — plugin broken, only solves auth not access |
| **Pull-verify** | OJS asks WP at access time (Subscription SSO plugin) | Eliminated — hijacks purchase flow |
| **Push-sync** | WP pushes to OJS via plugins on each side | **Chosen** |
| **Push-sync (direct DB)** | Same but writes to OJS DB directly | Fallback |
| **XML user import** | OJS built-in XML import (users only, not subscriptions) | Eliminated |
| **Janeway migration** | Replace OJS with Janeway + custom paywall | Genuine backup |

## Live URLs

- **Journal:** https://journal.existentialanalysis.org.uk/
- **Membership site:** https://community.existentialanalysis.org.uk/
- **Status page:** https://status.existentialanalysis.org.uk/

## Hard constraints

- **WP is source of truth** for membership. OJS is downstream.
- **Email is the matching key.** Same email required on both systems. No separate mapping table.
- **Bulk sync creates OJS accounts.** Don't wait for members to self-register.
- **OJS paywall must keep working** for non-member purchases (article, issue, back issue).
- **No OJS core modifications.** Plugins only.

## Plugins

Three custom OJS plugins, all bind-mounted into the OJS container via `docker-compose.yml`:

| Plugin | Directory | Purpose |
|---|---|---|
| **WP-OJS Subscription API** | `plugins/wpojs-subscription-api` | REST endpoints for user + subscription CRUD. Also adds login hint, paywall hint, footer messages. See [`docs/ojs-sync-plugin-api.md`](docs/ojs-sync-plugin-api.md). |
| **Inline HTML Galley** | `plugins/ojs-inline-html-galley` | Inlines HTML galley content on the article page. Shows subscriber/purchase access messages. |
| **Stripe Payment** | `plugins/stripe-payment` | Stripe Checkout for non-member purchases. Redirect flow + webhook handler. |

Plus a WP plugin (`plugins/wpojs-sync`) that hooks into WooCommerce Subscription lifecycle events.

### Stripe plugin details

Uses Stripe Checkout (redirect flow): buyer clicks purchase → OJS creates Checkout Session → redirects to Stripe → payment → redirects back → access granted. Webhook endpoint at `/payment/plugin/StripePayment/webhook` for async confirmation. Uses a restricted API key scoped to Checkout Sessions only.

Vendor deps (`plugins/stripe-payment/vendor/`) are gitignored. Stripe PHP SDK is installed via multi-stage Docker build (composer stage).

### OJS core patches

OJS ships with built-in plugins and behaviours that sometimes need modification. Since we can't change upstream code, we apply patches at Docker build time via PHP scripts in `docker/ojs/patches/`.

**How it works:**
1. Patch scripts live in `docker/ojs/patches/` — each is a self-contained PHP file that modifies OJS source files in place (via `str_replace` or `file_put_contents`).
2. The Dockerfile copies them to `/opt/ojs-patches/` and runs them at build time.
3. **Critically**, `entrypoint.sh` also runs all patches on every container start — because the `/var/www/html` volume overlays the Docker image, so build-time patches are lost.
4. All patches must be **idempotent** (`str_replace` is a no-op if the needle is already replaced; `file_put_contents` overwrites the whole file).
5. Each patch has a docblock explaining what it changes and why.

**When to use a patch vs other approaches:**
- **Patch** (this pattern): for OJS core or bundled plugin changes that can't be done via hooks. Survives container rebuilds. Fragile across OJS upgrades — each patch must be re-verified after upgrading OJS.
- **Custom plugin** (bind-mounted): for new functionality or when OJS provides hooks. Preferred when possible.
- **`setup-ojs.sh` + `plugin-states.json`**: for configuration (DB settings, plugin enable/disable). Not for code changes.

**Current patches:**
| Patch | What it does |
|---|---|
| `crossref-error-details.php` | Captures Crossref deposit error details (OJS 3.5 silently drops them) |
| `skip-remote-schema-validation.php` | Skips W3C schema validation that causes 13-second timeouts per DOI deposit |
| `recommend-by-similarity.php` | "Related articles" — 6 items, no pagination, title-first layout, simplified search link |
| `citation-style-dropdown.php` | Replaces custom citation format dropdown with native `<select>` — shows current format, standard UX |
| `recommend-by-author.php` | "Articles by the same author(s)" — 6 items, no pagination, title-first layout, no journal/issue links |

**To apply a patch to a running dev container** (without rebuilding):
```bash
sudo docker cp docker/ojs/patches/<name>.php pharkie-ojs-plugins-ojs-1:/opt/ojs-patches/
sudo docker exec pharkie-ojs-plugins-ojs-1 php /opt/ojs-patches/<name>.php
sudo docker exec pharkie-ojs-plugins-ojs-1 bash -c "rm -f /var/www/html/cache/t_compile/*.php"
```

## WP membership stack

**Ultimate Member + WooCommerce + WooCommerce Subscriptions.** UM handles registration/profiles/roles. WCS handles billing. Membership = WP role.

Primary integration: hook into **WooCommerce Subscriptions** status events (`woocommerce_subscription_status_active`, `_expired`, `_cancelled`, `_on-hold`). All sync calls are async (queued via Action Scheduler, not inline). Daily reconciliation catches any drift. See [`docs/wp-integration.md`](docs/wp-integration.md) for WCS hook details.

## Key documentation

| Doc | What |
|---|---|
| [`docs/ojs-sync-plugin-api.md`](docs/ojs-sync-plugin-api.md) | OJS plugin REST API reference |
| [`docs/ojs-internals.md`](docs/ojs-internals.md) | OJS native API, DB schema, PHP internals |
| [`docs/wp-integration.md`](docs/wp-integration.md) | WP membership stack, hooks, code patterns |
| [`docs/discovery.md`](docs/discovery.md) | Decision trail: what was tried and eliminated |
| [`docs/docker-setup.md`](docs/docker-setup.md) | Docker dev environment setup |
| [`docs/non-docker-setup.md`](docs/non-docker-setup.md) | Non-Docker plugin installation |
| [`docs/vps-deployment.md`](docs/vps-deployment.md) | VPS deployment guide |
| [`docs/support-runbook.md`](docs/support-runbook.md) | Support staff quick reference |
| [`docs/setup-guide.md`](docs/setup-guide.md) | Dev environment, secrets management (SOPS/age) |
| [`docs/backfill-pipeline.md`](docs/backfill-pipeline.md) | Backfill process guide for reviewers |
| [`docs/backfill-reference.md`](docs/backfill-reference.md) | Backfill technical reference |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Code conventions, testing, pre-commit hooks |
