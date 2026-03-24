# Pharkie OJS Plugins

A collection of plugins for [OJS](https://pkp.sfu.ca/software/ojs/) (Open Journal Systems) that fill gaps in OJS's built-in functionality: payment processing, inline article display, and membership sync with WordPress.

The plugins were built for a WordPress ↔ OJS integration where WP manages memberships via WooCommerce Subscriptions and OJS hosts a journal behind a paywall. See [ARCHITECTURE.md](ARCHITECTURE.md) for how the pieces fit together.

Each plugin works independently — install only what you need.

## Plugins

### [Stripe Payment](plugins/stripe-payment/)

Adds [Stripe](https://stripe.com) as a payment method for non-member article and issue purchases. Uses Stripe Checkout (hosted payment page) — no card data touches your server. Includes webhook handler for reliable payment confirmation.

- Stripe Checkout redirect flow (card, Apple Pay, Google Pay)
- Webhook endpoint with signature verification
- Test mode support (Stripe test keys)
- Amount + currency verification against OJS payment records

### [Inline HTML Galley](plugins/ojs-inline-html-galley/)

Renders HTML galley content directly on article landing pages. Readers with access see the full text immediately — no extra click to a separate viewer. Readers without access see a call-to-action with membership/purchase links.

- Inline rendering of "Full Text" HTML galleys
- Contextual access messages (member, subscriber, purchaser, admin)
- Non-subscriber CTA with membership and purchase links
- Hides galley links on issue TOC pages (readers click article titles instead)

### [WP-OJS Subscription API](plugins/wpojs-subscription-api/)

REST API for OJS user and subscription management — something OJS doesn't provide natively. Designed for push-sync integrations where an external system manages memberships.

- User CRUD (find, create, update email/password, GDPR delete)
- Subscription CRUD (create, expire, batch status lookup)
- WordPress password hash verification at login (members use their WP password on OJS)
- Configurable UI messages (login hint, paywall hint, footer)
- IP allowlisting + API key authentication

Paired with the [WP-OJS Sync](plugins/wpojs-sync/) WordPress plugin for automatic membership sync from WooCommerce Subscriptions.

### [WP-OJS Sync](plugins/wpojs-sync/) (WordPress)

WordPress plugin that hooks into WooCommerce Subscription lifecycle events and pushes changes to OJS via the Subscription API plugin above.

- Automatic sync on signup, renewal, cancellation, expiry
- Password and email change sync
- Bulk sync via WP-CLI for initial launch
- Async queue (Action Scheduler) with retry logic
- Daily reconciliation to catch drift
- Admin UI with sync log and connection testing

**WP-OJS Sync docs:**
[Docker setup](docs/docker-setup.md) · [VPS deployment](docs/vps-deployment.md) · [Non-Docker install](docs/non-docker-setup.md) · [WP admin guide](docs/wp-admin-reference.md) · [WP-CLI commands](docs/wp-cli-reference.md) · [Support runbook](docs/support-runbook.md) · [OJS plugin API](docs/ojs-sync-plugin-api.md) · [WP plugin internals](docs/wp-plugin-reference.md) · [Design decisions](docs/discovery.md)

## [Backfill Toolkit](backfill/)

Tools for digitising a journal's print archive into OJS. Takes whole-issue PDFs and produces per-article PDFs, HTML galleys, JATS XML, and OJS Native XML for import.

- PDF splitting into per-article files using table-of-contents metadata
- AI-powered HTML galley generation (Claude API)
- JATS 1.3 XML generation (single source of truth for article content)
- Citation extraction and classification (references vs. notes)
- OJS Native XML generation and import

**Backfill docs:**
[Process guide](docs/backfill-pipeline.md) · [Technical reference](docs/backfill-reference.md) · [TOC schema](docs/backfill-toc-guide.md)

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](./LICENSE.md).
