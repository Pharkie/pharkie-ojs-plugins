# WP OJS Sync

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

See the [Deployment guide](docs/deployment.md) for step-by-step installation, configuration, and troubleshooting for both plugins.

## CLI commands

All commands are available under the `wp ojs-sync` namespace.

```
wp ojs-sync sync [--dry-run] [--member=<id-or-email>] [--batch-size=<n>] [--delay=<ms>] [--yes]
```
Bulk sync all active members to OJS, or sync a single member. Use `--dry-run` to preview without making changes. `--yes` skips the confirmation prompt. `--delay` controls the pause between API calls (default 500ms). Unrecognised flags are rejected to prevent accidental bulk syncs (e.g. `--user=` instead of `--member=`).

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

## Pre-commit hooks

Run once after cloning (automatic in dev containers):

```bash
./setup-hooks.sh
```

This installs a pre-commit hook that checks for secrets, environment variable documentation, YAML syntax, and broken documentation links. To test manually: `./scripts/pre-commit`

## Docker dev environment

A `docker-compose.yml` is included that provides a local development setup with WordPress, OJS, and MariaDB. See [docker/README.md](./docker/README.md) for setup instructions and usage.

## E2E tests

Playwright browser tests in `e2e/` verify the full integration against the Docker dev environment (requires `--with-sample-data`). 10 spec files, 36 tests.

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

## Documentation

### Design and architecture

- [Implementation plan](docs/private/plan.md) — what we're building, how it works, endpoint specs, launch sequence, testing approach
- [Decision trail](docs/discovery.md) — what was tried, what was eliminated, and why
- [Plan review findings](docs/private/review-findings.md) — multi-perspective review and how findings were resolved

### Technical reference

- [OJS API reference](docs/ojs-api.md) — OJS REST API capabilities, DB schema, PHP internals
- [WP integration](docs/wp-integration.md) — WP membership stack (Ultimate Member + WooCommerce Subscriptions), hooks, code patterns
- [Janeway backup path](docs/private/janeway-paywall-investigation.md) — concrete technical plan if OJS 3.5 upgrade fails

### Deployment and operations

- [Deployment guide](docs/deployment.md) — non-Docker deployment: plugin installation, config, folder naming, troubleshooting
- [Hosting requirements](docs/private/hosting-requirements.md) — OJS + WP hosting specs, access needed for staging and production
- [Support runbook](docs/support-runbook.md) — quick reference for support staff handling member access issues
- [TODO / roadmap](TODO.md) — what's done, what's left before production

### Future planning

- [Membership platform comparison](docs/private/membership-platform.md) — WildApricot, CiviCRM, Beacon, Outseta evaluation

## AI disclosure

This codebase was generated by Claude Opus 4.6 (Anthropic) and has had limited manual code review. Use at your own risk. That said, the entire development process was closely overseen by an experienced developer with particular attention to security and reliability -- every architectural decision, endpoint design, and sync behaviour was directed and verified by a human throughout.

## License

PolyForm Noncommercial 1.0.0 -- see [LICENSE.md](./LICENSE.md).
