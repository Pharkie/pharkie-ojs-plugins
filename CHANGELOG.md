# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/).

## [v1.3.0](https://github.com/Pharkie/wp-ojs-sync/releases/tag/v1.3.0) — 2026-03-08

### Features

- Password hash sync — bulk sync and ongoing password changes push WP hashes to OJS; members log in with existing WP credentials (no separate "set your password" step)
- `--bulk` flag required for bulk sync (prevents accidental full syncs)
- Load-based backpressure replaces count-based rate limiting
- `scripts/generate-env.sh` — random credential generation for all environments
- Deployment tooling: `init-vps.sh`, `deploy.sh`, `smoke-test.sh`, `load-test.sh`
- Concurrent Playwright run protection (lockfile guard)

### Bug fixes

- Bulk sync not sending password hash (4th arg ignored)
- HPOS compatibility fix
- Deploy script env var validation

### Security

- All hardcoded dev passwords eliminated — Docker Compose fails fast if secrets unset
- `scripts/generate-env.sh` generates random credentials for every environment

### Docs

- Plugin reference split into 3 docs (WP-CLI, WP admin, plugin reference)
- OJS API reference split from internals
- README restructured with mermaid architecture diagram
- Staging/prod setup, email setup, hosting benchmark docs

### 58 E2E tests passing

## [v1.2.5](https://github.com/Pharkie/wp-ojs-sync/releases/tag/v1.2.5) — 2026-03-04

- Admin page smoke tests
- Fix `init-vps.sh` SSH wait loop
- Fix `.env` permissions in deploy script

## [v1.2.0](https://github.com/Pharkie/wp-ojs-sync/releases/tag/v1.2.0) — 2026-03-04

### Settings page overhaul

- WP admin settings page restructured for clarity
- OJS subscription type fields now populated via dropdowns from the OJS API
- Connection status shown automatically on page load (replaces manual Test Connection button)

### OJS plugin: subscription-types API

- New `GET /wpojs/subscription-types` endpoint — returns type IDs with human-readable names

### Dev environment

- UM roles registered with display names matching production
- Script renamed: `seed-subscriptions.php` → `setup-and-sample-data.php`
- Journal name "Existential Analysis" configured automatically with `--with-sample-data`

### Bug fixes

- OJS UI message corruption — moved plugin settings from `config.inc.php` to `plugin_settings` DB table
- E2E test cleanup — fixed GDPR-anonymised user cleanup, suppressed textdomain notice

### 35 E2E tests passing

## [v1.1.1](https://github.com/Pharkie/wp-ojs-sync/releases/tag/v1.1.1) — 2026-03-03

First release validated on staging with real member data.

### Bug fixes

- `hash_equals()` TypeError on numeric secrets — PHP INI parser returns int for numeric-only config values; now cast to `(string)`
- `checkRateLimit()` crash when `wpojs_api_log` table missing — wrapped in try/catch
- `getInstallSchemaFile()` is `final` in OJS 3.5 — replaced with `getInstallMigration()` using Laravel Migration class
- Preflight didn't check for API log table or subscription types — added both checks
- CLI `sync` rejects unknown flags — `--user=` now errors instead of silently running a full bulk sync
- `--user=` → `--member=` corrected in all docs

### New

- Non-Docker deployment guide (`docs/non-docker-setup.md`)
- Launch sequence updated with explicit subscription type setup steps

### 35 E2E tests passing
