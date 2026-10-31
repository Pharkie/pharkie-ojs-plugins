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

### Fence

`WPOJS_FENCE_EMAIL_PATTERN` (a PCRE) limits which OJS accounts this install may **write** to. When it is defined, every write — find-or-create, subscription, expire, email change, password change, delete — is refused unless the account's email matches. Reads (ping, lookups, the reconciliation's status checks) are unaffected.

```php
define('WPOJS_FENCE_EMAIL_PATTERN', '/^[^@\s]+@test\.invalid\z/i');
```

It exists for the WordPress test rig when it runs on a server beside the **live** journal (profile `wp-rig`; the SEA box runs it no longer), where it held a March copy of real members, each linked to their real journal account. Without it, any change made to a member on the rig — a lapse, an email edit, a deletion, an overdue cron — reaches that member's live account within a day. The rig's `wordpress/config/environments/staging.php` sets the pattern above, hard-coded rather than read from the environment (it applies whenever `WP_ENV=staging`, which `docker-compose.staging.yml` pins). `.invalid` is a reserved TLD, so no real member can be inside it; the smoke test's `smoke-test-<epoch>@test.invalid` is. **Never define it on the real WordPress.**

How it holds:

- It is enforced in `WPOJS_API_Client`, the only code that talks to OJS, so a code path that forgets to check is refused rather than let through.
- An id-keyed write (everything except find-or-create) needs OJS itself to have said, in the same process, that the id belongs to a matching email — from its user lookup, never from what find-or-create returned or what WordPress has cached. The WordPress side is not trusted for that: a rig user's email can be edited to a test address while its cached `_wpojs_user_id` still points at a real member.
- A defined but empty or invalid pattern admits nothing.
- The sync handlers check first and log a refusal as status `fenced` in the sync log — no retry, no admin alert. Reconciliation still checks every member against OJS but queues nothing outside the fence, and reports how many changes it withheld.
- `wp ojs-sync fence` proves it: it attempts every write against an account outside the fence with every request except a GET blocked, checks that a real linked account cannot be vouched for (with its own email, and with a test address), and fails if anything got as far as a write.
- Known, accepted: vouching checks the email WordPress believes the account has. Two rare cases on **test** accounts are therefore refused (logged `fenced`) when they would have been allowed: an email changed twice before the first change synced, and a delete retried after OJS had already anonymised the account. Neither touches a real member.

Tests: `php plugins/wpojs-sync/tests/fence-test.php` (no WordPress or PHPUnit needed).

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
