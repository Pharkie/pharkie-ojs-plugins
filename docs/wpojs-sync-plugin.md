# WP-OJS Sync (WordPress Plugin)

WordPress plugin that automatically syncs WooCommerce Subscription membership status to OJS (Open Journal Systems). When a member signs up, renews, cancels, or expires, the plugin pushes the change to OJS via the [WP-OJS Subscription API](wpojs-subscription-api-plugin.md) plugin.

## Requirements

- WordPress 5.6+, PHP 7.4+
- WooCommerce + WooCommerce Subscriptions
- Action Scheduler (bundled with WooCommerce)
- The [WP-OJS Subscription API](wpojs-subscription-api-plugin.md) plugin installed on OJS

## Installation

1. Copy `plugins/wpojs-sync` to `wp-content/plugins/wpojs-sync/` (or the equivalent in your WordPress setup).
2. Activate in **Plugins → Installed Plugins**.
3. Configure in **Settings → OJS Sync**: OJS base URL, API key, subscription type mapping.

## Features

- **Automatic sync:** Hooks into WooCommerce Subscription lifecycle events (`active`, `expired`, `cancelled`, `on-hold`).
- **Password sync:** Pushes WP password hashes to OJS so members can log in with one password.
- **Email change sync:** Updates OJS email when a member changes their WP email.
- **Bulk sync:** WP-CLI command (`wp ojs-sync bulk-sync`) for initial launch — creates OJS accounts for all existing members.
- **Async queue:** All sync operations go through Action Scheduler with retry logic and exponential backoff.
- **Daily reconciliation:** Scheduled job catches any drift between WP and OJS.
- **Admin UI:** Settings page, sync log with filters, connection testing, manual sync triggers.
- **WP-CLI:** `wp ojs-sync test-connection`, `wp ojs-sync bulk-sync`, `wp ojs-sync sync-user`.

## Configuration

Set the API key as a constant in `wp-config.php`:

```php
define('WPOJS_API_KEY', 'your-shared-secret-here');
```

The key must match the `WPOJS_API_KEY_SECRET` environment variable on the OJS server. See the [OJS plugin guide](wpojs-subscription-api-plugin.md) for server-side configuration.

## Sub-docs

The sync story spans WordPress admin, WP-CLI, deployment, and the OJS API — split by aspect:

- [Docker setup](docker-setup.md) — dev environment with WP + OJS in Docker
- [VPS deployment](vps-deployment.md) — production deployment to a Linux VPS
- [Non-Docker install](non-docker-setup.md) — bare-metal install without Docker
- [WP admin guide](wp-admin-reference.md) — the plugin's settings UI and sync log
- [WP-CLI commands](wp-cli-reference.md) — `wp ojs-sync test-connection`, `bulk-sync`, `sync-user`
- [Support runbook](support-runbook.md) — triage flowchart for common sync issues
- [OJS plugin API](ojs-sync-plugin-api.md) — REST endpoint reference the sync plugin calls into
- [WP plugin internals](wp-plugin-reference.md) — code structure, hooks, queue architecture
- [Design decisions](discovery.md) — decision trail: what was tried, eliminated, and why

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../LICENSE.md).
