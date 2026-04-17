# Stripe Payment Plugin for OJS

Adds [Stripe](https://stripe.com) as a payment method for non-member article and issue purchases. Uses Stripe Checkout (hosted payment page) — no card data touches your server. Includes webhook handler for reliable payment confirmation.

## Requirements

- OJS 3.5+
- PHP 8.1+ with `curl` extension
- Stripe account ([dashboard.stripe.com](https://dashboard.stripe.com))

## Documentation

See **[docs/stripe-payment-plugin.md](../../docs/stripe-payment-plugin.md)** for the full guide — installation, Checkout flow, webhook setup, test mode, environment variables, security, troubleshooting.

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
