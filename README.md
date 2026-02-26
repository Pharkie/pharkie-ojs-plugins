# WP OJS Sync -- WordPress <-> OJS Membership Sync

A pair of plugins (WordPress + OJS) that sync membership data from WordPress (via WooCommerce Subscriptions) to Open Journal Systems. Members get journal access automatically; non-members can still buy content via OJS's built-in paywall.

## How it works

WP OJS Sync uses a **push-sync** architecture. WordPress is the source of truth for membership. A custom OJS plugin exposes REST endpoints for user and subscription CRUD (OJS has no native subscription API). The WP plugin calls those endpoints whenever membership status changes.

Two modes of operation:

1. **Initial bulk sync:** A WP-CLI command reads all active WooCommerce Subscriptions, creates OJS user accounts and subscription records for each member, then sends "set your password" welcome emails. This is how existing members get access at launch.

2. **Ongoing sync:** The WP plugin hooks into WooCommerce Subscription lifecycle events (active, expired, cancelled, on-hold) and pushes changes to OJS automatically via an Action Scheduler queue with retries and daily reconciliation.

```
Member signs up / renews on WordPress
  -> WCS status changes to active -> WP plugin queues sync
  -> Action Scheduler calls OJS: find-or-create user + create subscription
  -> OJS paywall sees subscription -> access granted

Member lapses / cancels / payment fails
  -> WCS status changes -> WP plugin queues expire
  -> Action Scheduler calls OJS: expire subscription
  -> OJS paywall denies access -> shows purchase options

Non-member visits paywalled content
  -> No subscription -> normal OJS purchase flow
```

## Prerequisites

- WordPress 5.6+, PHP 7.4+
- WooCommerce + WooCommerce Subscriptions
- Action Scheduler (bundled with WooCommerce)
- OJS 3.5+ (the OJS plugin requires the 3.5 plugin API)

## Installation

### WP plugin

1. Copy `plugins/wpojs-sync/` to `wp-content/plugins/wpojs-sync/`
2. Activate the plugin in WP Admin -> Plugins

### OJS plugin

1. Copy `plugins/wpojs-subscription-api/` to your OJS installation at `plugins/generic/wpojsSubscriptionApi/`
2. Enable the plugin in OJS Admin -> Website -> Plugins -> Generic

### Configure the API key

Add the following to your `wp-config.php`:

```php
define('WPOJS_API_KEY', 'your-shared-secret-key');
```

This key is sent as a Bearer token in all requests from WP to OJS.

### Configure OJS

Add a `[wpojs]` section to your OJS `config.inc.php`:

```ini
[wpojs]
allowed_ips = "203.0.113.10"
wp_member_url = "https://your-wp-site.example.com/membership/"
support_email = "support@example.com"
```

### Configure WP settings

Navigate to **Settings -> OJS Sync** in WP Admin and configure:

- OJS Base URL
- Subscription Type Mapping (WooCommerce Product -> OJS Subscription Type ID)
- Default Subscription Type ID
- Manual Member Roles
- Journal Display Name

## Configuration reference

### WP settings page (Settings -> OJS Sync)

| Setting | Description |
|---|---|
| OJS Base URL | Root URL of your OJS installation (e.g. `https://journal.example.com`) |
| Subscription Type Mapping | Maps WooCommerce Subscription product IDs to OJS subscription type IDs |
| Default Type ID | Fallback OJS subscription type ID when no mapping matches |
| Manual Member Roles | WP roles that should be synced even without a WCS subscription |
| Journal Display Name | Journal name used in welcome emails and admin UI |

### OJS config.inc.php `[wpojs]` section

| Key | Description |
|---|---|
| `allowed_ips` | Comma-separated IP addresses allowed to call the plugin endpoints |
| `wp_member_url` | URL to link OJS users back to the WP membership page |
| `support_email` | Support contact shown in error responses and emails |

### WP constant

| Constant | Location | Description |
|---|---|---|
| `WPOJS_API_KEY` | `wp-config.php` | Shared secret used as Bearer token for WP -> OJS API calls |

## CLI commands

All commands are available under the `wp ojs-sync` namespace.

```
wp ojs-sync sync [--dry-run] [--user=<id-or-email>] [--batch-size=<n>] [--delay=<ms>]
```
Bulk sync all active members to OJS, or sync a single user. Use `--dry-run` to preview without making changes. `--delay` controls the pause between API calls (default 500ms); increase on slow environments.

```
wp ojs-sync send-welcome-emails [--dry-run]
```
Send "set your password" emails to synced members who haven't received one yet.

```
wp ojs-sync reconcile
```
Run reconciliation to detect and fix drift between WP and OJS subscription state.

```
wp ojs-sync status
```
Show sync status summary and Action Scheduler queue stats.

```
wp ojs-sync test-connection
```
Verify OJS connectivity, API key validity, and version compatibility.

## Docker dev environment

A `docker-compose.yml` is included that provides a local development setup with WordPress, OJS, and MariaDB. See [docker/README.md](./docker/README.md) for setup instructions and usage.

## E2E tests

Playwright browser tests in `e2e/` verify the full integration against the Docker dev environment (requires `--with-sample-data`). 11 spec files, 29 tests total.

```bash
npm install && npx playwright install chromium
npm test                    # all tests (headless, ~4 min)
npm run test:headed         # watch in browser
npx playwright test e2e/tests/wp-dashboard.spec.ts  # single file
```

The **expanded resilience suite** (`expanded-resilience.spec.ts`, 19 tests) covers edge cases: out-of-order events, multiple subscriptions, rapid status changes, user data edge cases, reconciliation drift detection, welcome email dedup, and OJS API validation. It runs as part of `npm test` but can also be run separately:

```bash
npx playwright test expanded-resilience     # just the resilience suite
```

## Architecture

For the full implementation plan, see [docs/plan.md](./docs/plan.md).

For the decision trail -- what was tried, what was eliminated, and why -- see [docs/discovery.md](./docs/discovery.md).

## Repository structure

```
.
├── README.md
├── LICENSE.md
├── CLAUDE.md                              # AI assistant instructions
├── TODO.md                                # Task checklist
├── docker-compose.yml                     # Dev environment
├── docs/
│   ├── plan.md                            # Implementation plan
│   ├── discovery.md                       # Decision trail
│   ├── review-findings.md                 # Plan review findings
│   ├── ojs-api.md                         # OJS REST API reference
│   ├── wp-integration.md                  # WP membership stack details
│   ├── hosting-requirements.md            # OJS + WP hosting and access requirements
│   └── support-runbook.md                 # Support staff quick reference
├── plugins/
│   ├── wpojs-sync/                        # WP plugin
│   └── wpojs-subscription-api/            # OJS plugin
├── playwright.config.ts                   # Playwright config (testDir: e2e/tests)
├── e2e/                                   # Playwright E2E browser tests
├── docker/                                # Docker config and Dockerfiles
├── scripts/                               # Setup and bootstrap scripts
├── discovery-screenshots/                 # WP admin pages, plugin configs (from audit phase)
└── launch-comms/                          # Welcome email copy, member FAQ, support runbook
```

## AI disclosure

This codebase was generated by Claude Opus 4.6 (Anthropic) and has had limited manual code review. Use at your own risk. That said, the entire development process was closely overseen by an experienced developer with particular attention to security and reliability -- every architectural decision, endpoint design, and sync behaviour was directed and verified by a human throughout.

## License

PolyForm Noncommercial 1.0.0 -- see [LICENSE.md](./LICENSE.md).
