# WP-OJS Sync (WordPress Plugin)

WordPress plugin that automatically syncs WooCommerce Subscription membership status to OJS (Open Journal Systems). When a member signs up, renews, cancels, or expires, the plugin pushes the change to OJS via the [WP-OJS Subscription API](../wpojs-subscription-api/) plugin.

## Requirements

- WordPress 5.6+, PHP 7.4+
- WooCommerce + WooCommerce Subscriptions
- Action Scheduler (bundled with WooCommerce)
- The [WP-OJS Subscription API](../wpojs-subscription-api/) plugin installed on OJS

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

The key must match the `WPOJS_API_KEY_SECRET` environment variable on the OJS server. See the [OJS plugin guide](../../docs/wpojs-subscription-api-plugin.md) for server-side configuration.

## Documentation

- [WP admin guide](../../docs/wp-admin-reference.md)
- [WP-CLI commands](../../docs/wp-cli-reference.md)
- [Support runbook](../../docs/support-runbook.md)
- [WP plugin internals](../../docs/wp-plugin-reference.md)
- [Docker setup](../../docs/docker-setup.md)
- [VPS deployment](../../docs/vps-deployment.md)
- [Non-Docker install](../../docs/non-docker-setup.md)

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
