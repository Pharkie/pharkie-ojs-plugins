# Docker Setup Guide

> Everything runs in containers via Docker Compose. The same stack works locally, on staging, and in production — only the `.env` file and compose overrides differ.

For deploying to a VPS, see [`docs/vps-deployment.md`](../docs/vps-deployment.md). For installing the plugins on existing non-Docker servers, see [`docs/non-docker-setup.md`](../docs/non-docker-setup.md).

## Getting started

```bash
# 1. Build and start
docker compose up -d --build

# 2. Wait for OJS auto-install (check logs)
docker compose logs -f ojs

# 3. Run setup scripts (--with-sample-data imports test users + articles)
docker compose exec wp bash /var/www/html/scripts/setup-wp.sh --with-sample-data
docker compose exec ojs bash /scripts/setup-ojs.sh --with-sample-data
```

## URLs

| Service | Browser URL | Internal (container-to-container) |
|---------|------------|-----------------------------------|
| WordPress | http://localhost:8080 | http://wp:80 |
| WP admin | http://localhost:8080/wp/wp-admin/ | — |
| OJS | http://localhost:8081 | http://ojs:80 |
| OJS journal | http://localhost:8081/index.php/journal | http://ojs:80/index.php/journal |
| OJS API (from WP) | — | http://ojs:80/index.php/journal/api/v1/wpojs/... |

## Credentials

> **All passwords are randomly generated** — there are no default passwords. Run `scripts/generate-env.sh` to create your `.env`, or let `rebuild-dev.sh` / `setup.sh --env=dev` do it automatically. Check your `.env` file for the actual values.

| | WordPress | OJS |
|--|-----------|-----|
| Admin user | `admin` / check `.env` `WP_ADMIN_PASSWORD` | `admin` / check `.env` `OJS_ADMIN_PASSWORD` |
| DB host | `wp-db` | `ojs-db` |
| DB name | `wordpress` | `ojs` |
| DB user | `wordpress` | `ojs` |
| DB password | check `.env` `DB_PASSWORD` | check `.env` `OJS_DB_PASSWORD` |
| Root password | check `.env` `WP_DB_ROOT_PASSWORD` | check `.env` `OJS_DB_ROOT_PASSWORD` |

API key for WP → OJS sync: check `.env` `WPOJS_API_KEY`.

## Commands

```bash
# Start
docker compose up -d

# Start + rebuild images
docker compose up -d --build

# Stop
docker compose down

# Stop and delete all data (full fresh start)
docker compose down -v

# Reset OJS only (wipes DB + volumes, reinstalls automatically)
./docker/reset-ojs.sh

# Run WP setup (install core, activate plugins, set options)
docker compose exec wp bash /var/www/html/scripts/setup-wp.sh
# ...with ~1400 anonymised test users + WCS subscriptions:
docker compose exec wp bash /var/www/html/scripts/setup-wp.sh --with-sample-data

# Run OJS setup (create journal, subscription type, enable plugin, enable paywall)
docker compose exec ojs bash /scripts/setup-ojs.sh
# ...with 2 issues + 43 articles from live (issues set to require subscription):
docker compose exec ojs bash /scripts/setup-ojs.sh --with-sample-data

# View logs
docker compose logs -f        # all
docker compose logs -f ojs    # one service

# Shell into a container
docker compose exec wp bash
docker compose exec ojs bash

# Test WP → OJS connectivity
docker compose exec wp curl http://ojs:80/index.php/journal/api/v1/wpojs/ping
```

## Sample data (`--with-sample-data`)

The `--with-sample-data` flag on `setup-wp.sh` runs a three-step pipeline that populates the dev environment with realistic membership data from the live site:

| Step | Script | What it does | Speed |
|------|--------|-------------|-------|
| 1. Import users | `wp user import-csv` | Imports ~1,400 anonymised users from `docker/test-users.csv` as `subscriber` | ~2 min |
| 2. Apply roles | `scripts/apply-roles.php` | Reads `original_role` column from CSV, updates `wp_usermeta` directly (UM roles can't be assigned via `wp user import-csv`) | ~2s |
| 3. Seed sample data | `scripts/setup-and-sample-data.php` | Creates 6 WC subscription products, batch-inserts WCS subscription records for ~683 members with `um_custom_role_1–6`, and configures wpojs_* plugin options (type mapping, member roles, journal name) | ~1.5s |

Step 3 inserts the minimum rows needed for `wcs_get_subscriptions()` to find active subscriptions: `wp_posts` (subscription post), `wp_postmeta` (dates + customer), `wp_woocommerce_order_items` (line item), and `wp_woocommerce_order_itemmeta` (product ID). It also configures the `wpojs_*` plugin options (type mapping, member roles, manual roles, journal name).

**Result:** `wp ojs-sync status` shows ~684 active members (683 WCS + 1 manual role). The environment is ready for `wp ojs-sync sync --bulk`.

All three steps are idempotent — running `setup-wp.sh --with-sample-data` again skips already-imported data.

> **When in doubt, reset.** OJS stores state in three places (database, code volume, config file). If any one is stale, things break in confusing ways. This script handles all three.

## Resetting OJS

If OJS gets into a broken state (failed install, version change, corrupt DB), run:

```bash
./docker/reset-ojs.sh
```

This handles everything in one shot:
1. Stops containers
2. Deletes the `ojs_data` volume (code) and `ojs_files` volume
3. Resets the OJS database
4. Starts containers — entrypoint generates config + auto-installs
5. Run `setup-ojs.sh` to create journal + subscription type

**Why all three steps matter:** OJS has state in three places — the database, the code volume (`ojs_data`), and `config.inc.php` (inside `ojs_data`). If any one of these is stale, the install will fail. The reset script handles all three.

**Changing OJS image version:** Docker does not update named volumes when you change image tags. If you change the OJS image in `docker-compose.yml`, you **must** run `./docker/reset-ojs.sh` — otherwise the old code stays in the volume and you get a version mismatch.

## Architecture

| Service | Image / Build | App version | Notes |
|---------|---------------|-------------|-------|
| WordPress | Custom `docker/wp/Dockerfile` (from `php:8.2-apache`) | WP 6.9.1 (Bedrock), PHP 8.2 | Composer-managed deps |
| OJS | Custom `docker/ojs/Dockerfile` (from `pkpofficial/ojs:3_5_0-3`) | OJS 3.5.0.3, PHP 8.3 | Adds envsubst + mariadb client |
| MariaDB (both) | `mariadb:10.11` | 10.11 | — |

Do not use Docker tag `3_5_0-2` — it has a [known install bug](https://github.com/pkp/pkp-lib/issues/12144).

### WP: Bedrock layout

WordPress uses [Bedrock](https://roots.io/bedrock/) — Composer manages WP core + plugins, config reads from `.env`.

```
wordpress/
├── composer.json         # Pins WP core + all plugins
├── composer.lock         # Locked versions (committed)
├── config/
│   └── application.php   # Main config — reads .env
├── web/
│   ├── wp/               # WP core (gitignored, Composer-managed)
│   ├── app/plugins/      # Plugins (gitignored, Composer-managed)
│   ├── index.php         # Bedrock loader
│   └── wp-config.php     # Tiny delegator to config/application.php
└── wp-cli.yml            # Tells WP-CLI where WP core lives
```

### OJS: config templating

OJS config is generated from `docker/ojs/config.inc.php.tmpl` by the entrypoint script. Environment variables are substituted at container start. OJS installs automatically on first boot (no wizard).

## How it works

The main `docker-compose.yml` lives in the **project root** (not this directory). Dockerfiles and config templates live here in `docker/`. Run all commands from the project root.

`docker compose up -d` starts four containers — WordPress + its MariaDB, and OJS + its MariaDB — on a shared Docker network (`sea-net`) so WP can call OJS API endpoints directly.

**WordPress** uses a Bedrock layout (see below). The `wpojs-sync` plugin is bind-mounted from `plugins/wpojs-sync/` into the WP plugins directory, so edits are reflected immediately.

**OJS** starts from an official OJS Docker image. The first time you start it, it automatically sets up its database and creates an admin account — no manual install wizard needed. The custom `wpojs-subscription-api` plugin is mounted into the container from your local `plugins/` folder, just like the WP plugin. OJS also needs the plugin's API route files copied into a specific location (`api/v1/wpojs/`) — Docker Compose handles this automatically via a bind mount.

Setup scripts (`setup-wp.sh`, `setup-ojs.sh`) run after containers are up to create the journal, configure subscription types, activate plugins, and optionally import sample data. See [Getting started](#getting-started) above.

All credentials come from `.env` (generated by `scripts/generate-env.sh`). Docker Compose fails fast if required secrets are missing.

> **Live editing works out of the box.** Both plugins are mounted from your local filesystem — save a file and refresh the browser.

## Bind-mounted plugins

Edits on your host are reflected immediately in the containers:

| Host path | Container path |
|-----------|---------------|
| `plugins/wpojs-sync/` | WP: `/var/www/html/web/app/plugins/wpojs-sync/` |
| `plugins/wpojs-subscription-api/` | OJS: `/var/www/html/plugins/generic/wpojsSubscriptionApi/` |

## Environment variables

All config is in `.env` (gitignored). See `.env.example` for the full list. Key groups:

- **WP (Bedrock):** `DB_*`, `WP_HOME`, `WP_SITEURL`, `WP_ENV`, auth salts
- **WP-OJS integration:** `WPOJS_API_KEY`, `WPOJS_BASE_URL`
- **OJS:** `OJS_DB_*`, `OJS_BASE_URL`, `OJS_TIMEZONE`, `OJS_ADMIN_*`
- **OJS email:** `OJS_SMTP_*`, `OJS_MAIL_FROM`
- **OJS security:** `WPOJS_API_KEY_SECRET`, `WPOJS_ALLOWED_IPS`
- **WP-OJS UI messages:** `WPOJS_WP_MEMBER_URL`, `WPOJS_SUPPORT_EMAIL`
