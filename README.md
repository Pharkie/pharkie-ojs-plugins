<table><tr><td>

# WP OJS Sync

**The problem:** An organisation runs its membership and payments through [WordPress](https://wordpress.org/) (using WooCommerce Subscriptions), and publishes an academic journal on [OJS](https://pkp.sfu.ca/software/ojs/) (Open Journal Systems). Members should get journal access automatically when they pay, and lose it when they cancel — but these are two separate systems with no built-in connection. OJS has no subscription API, so there's no obvious way to bridge them.

**What this repo does:** A set of plugins — two for WordPress, three for OJS — that keep the two systems in sync and improve the journal reading experience. At launch, a bulk sync creates OJS accounts for all existing members (with their WordPress password hashes, so they can log in immediately). After that, the WP plugin automatically pushes changes to OJS whenever a member signs up, renews, cancels, or expires. Non-members can buy individual articles via Stripe checkout.

</td><td width="45%">

<img src="docs/images/wp-settings-page.png" alt="OJS Sync settings page in WordPress admin">

</td></tr></table>

## How it works

WordPress is the source of truth for membership. The WP plugin hooks into WooCommerce Subscription lifecycle events and pushes changes to OJS via a custom REST API. All sync is async (Action Scheduler), with daily reconciliation to catch drift.

Bulk sync creates OJS accounts with WP password hashes — members log in to OJS with their existing WP password, no "set your password" step.

```mermaid
flowchart LR
    A[Member signs up / renews] -->|WCS status → active| B[WP queues sync]
    B --> C[Action Scheduler]
    C -->|find-or-create user + subscription| D[OJS grants access]

    E[Member cancels / expires] -->|WCS status change| F[WP queues expire]
    F --> C
    C -->|expire subscription| G[OJS revokes access]

    H[Member changes password] --> I[WP queues password sync]
    I --> C
    C -->|update password hash| J[OJS accepts new password]
```

## Plugins

| Plugin | Platform | Directory | Purpose |
|---|---|---|---|
| **WP-OJS Sync** | WordPress | `plugins/wpojs-sync` | Hooks into WooCommerce Subscription events, pushes user + subscription changes to OJS. Admin UI, WP-CLI commands, Action Scheduler queue. |
| **WP-OJS Subscription API** | OJS | `plugins/wpojs-subscription-api` | REST API for user and subscription CRUD (OJS has none natively). WP password hash verification at login. Configurable UI messages. [README](plugins/wpojs-subscription-api/README.md) · [API reference](docs/ojs-sync-plugin-api.md) |
| **Stripe Payment** | OJS | `plugins/stripe-payment` | Stripe Checkout for non-member article/issue purchases. Redirect flow with webhook confirmation. Replaces PayPal. [README](plugins/stripe-payment/README.md) |
| **Inline HTML Galley** | OJS | `plugins/ojs-inline-html-galley` | Renders HTML article content inline on the landing page. Access messages for members, purchasers, and non-subscribers. [README](plugins/ojs-inline-html-galley/README.md) |

## Documentation

**Getting started** — pick your path:
- [Run locally](docs/docker-setup.md) — Docker stack on your machine
- [Deploy to a server](docs/vps-deployment.md) — provision a VPS, deploy the Docker stack
- [Existing servers](docs/non-docker-setup.md) — install plugins without Docker

**Using the plugins** — [WP admin guide](docs/wp-admin-reference.md) · [WP-CLI commands](docs/wp-cli-reference.md) · [Support runbook](docs/support-runbook.md)

**Reference** — [WP plugin internals](docs/wp-plugin-reference.md) · [OJS plugin API](docs/ojs-sync-plugin-api.md) · [OJS plugin internals](docs/ojs-plugin-internals.md) · [Hosting requirements](docs/private/hosting-requirements.md)

**Design** — [Implementation plan](docs/private/plan.md) · [Decision trail](docs/discovery.md) · [OJS native internals](docs/ojs-internals.md) · [WP integration notes](docs/wp-integration.md) · [Plan review findings](docs/private/review-findings.md) · [Janeway backup path](docs/private/janeway-paywall-investigation.md) · [TODO / roadmap](TODO.md)

## Prerequisites

> If you're just exploring, the [Docker setup](docs/docker-setup.md) runs the full stack locally with no external dependencies.

- WordPress 5.6+, PHP 7.4+
- WooCommerce + WooCommerce Subscriptions
- Action Scheduler (bundled with WooCommerce)
- OJS 3.5+ (the OJS plugin requires the 3.5 plugin API)

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 -- see [LICENSE.md](./LICENSE.md).
