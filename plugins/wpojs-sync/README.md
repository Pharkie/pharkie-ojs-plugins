# WP-OJS Sync (WordPress Plugin)

WordPress plugin that automatically syncs WooCommerce Subscription membership status to OJS (Open Journal Systems). When a member signs up, renews, cancels, or expires, the plugin pushes the change to OJS via the WP-OJS Subscription API plugin.

## Requirements

- WordPress 5.6+, PHP 7.4+
- WooCommerce + WooCommerce Subscriptions
- Action Scheduler (bundled with WooCommerce)
- The WP-OJS Subscription API plugin installed on OJS

## Documentation

See **[docs/wpojs-sync-plugin.md](../../docs/wpojs-sync-plugin.md)** for the overview, with sub-docs covering Docker setup, VPS deployment, non-Docker install, WP admin guide, WP-CLI reference, support runbook, WP plugin internals, and design decisions.

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
