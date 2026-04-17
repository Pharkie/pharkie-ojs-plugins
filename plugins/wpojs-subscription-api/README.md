# WP-OJS Subscription API Plugin for OJS

REST API for OJS user and subscription management — something OJS doesn't provide natively. Designed for push-sync integrations where an external system (e.g. WordPress with WooCommerce Subscriptions) manages memberships and pushes changes to OJS.

Also handles WordPress password hash verification at login (so members can use their WP password on OJS without resetting) and adds configurable UI messages (login hint, paywall hint, site footer).

## Requirements

- OJS 3.5+
- At least one subscription type configured in OJS

## Documentation

- **[docs/wpojs-subscription-api-plugin.md](../../docs/wpojs-subscription-api-plugin.md)** — full plugin guide: installation, configuration, WordPress password hashes, authentication, Docker deployment
- **[docs/ojs-sync-plugin-api.md](../../docs/ojs-sync-plugin-api.md)** — REST API endpoint reference (request/response schemas)

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
