# Stripe Payment Plugin for OJS

Adds Stripe as a payment method for OJS journals. Non-subscribers can purchase individual articles or issues via Stripe Checkout — a hosted payment page that handles card input, 3D Secure, and PCI compliance. No card data touches your server.

## Requirements

- OJS 3.5+
- PHP 8.1+ with `curl` extension
- Stripe account ([dashboard.stripe.com](https://dashboard.stripe.com))

## Installation

1. Copy the plugin folder to `plugins/paymethod/stripe/` in your OJS installation.
2. Install PHP dependencies:
   ```bash
   cd plugins/paymethod/stripe
   composer install --no-dev
   ```
3. Register the plugin in the OJS database:
   ```sql
   INSERT INTO versions (major, minor, revision, build, date_installed, current, product_type, product, product_class_name, lazy_load, sitewide)
   VALUES (1, 0, 0, 0, NOW(), 1, 'plugins.paymethod', 'stripe', '', 0, 0);
   ```
4. Clear the OJS cache: `rm -rf cache/fc-* cache/wc-* cache/opcache`

## Configuration

1. Go to **Settings → Distribution → Payments**.
2. Check **Enable Payments** and set currency.
3. Select **Stripe Payment** from the Payment Plugins dropdown.
4. Enter your Stripe API keys:
   - **Secret Key** — `sk_test_...` (test) or `sk_live_...` (production)
   - **Publishable Key** — `pk_test_...` or `pk_live_...`
   - **Webhook Signing Secret** — `whsec_...` (optional but recommended, see below)
5. Save.

Set article/issue purchase fees under **Payments → Payment Types**.

### Test vs live mode

Stripe routes to test or live based on the key prefix — no endpoint switching needed. Use `sk_test_` keys during development; switch to `sk_live_` for production. The **Test Mode** checkbox is informational only (shown in the admin UI).

Test card: `4242 4242 4242 4242`, any future expiry, any CVC.

## How it works

### Payment flow

1. Non-subscriber clicks a paywalled article galley (PDF or Full Text).
2. OJS creates a `QueuedPayment` record and calls the plugin.
3. The plugin creates a Stripe Checkout Session via the API and redirects the buyer to Stripe's hosted checkout page.
4. Buyer completes payment on Stripe (card, Apple Pay, Google Pay — whatever you've enabled in your Stripe dashboard).
5. Stripe redirects back to OJS with the session ID.
6. The plugin verifies the payment server-side (fetches the session from Stripe, checks amount/currency match) and grants article access.

### Webhook (recommended)

The redirect callback handles the happy path, but if the buyer closes their browser after paying (before the redirect completes), the payment is captured by Stripe but OJS doesn't know. The webhook catches this:

1. In your Stripe dashboard, add a webhook endpoint:
   ```
   https://your-ojs-domain/index.php/YOUR_JOURNAL_PATH/payment/plugin/StripePayment/webhook
   ```
2. Subscribe to the `checkout.session.completed` event.
3. Copy the signing secret (`whsec_...`) into the plugin settings.

The webhook handler verifies the Stripe signature (rejects unsigned/replayed requests), checks if the payment was already fulfilled (idempotent), and grants access if not.

## Docker deployment

If using Docker, bind-mount the plugin directory:

```yaml
# docker-compose.yml
services:
  ojs:
    volumes:
      - ./plugins/stripe-payment:/var/www/html/plugins/paymethod/stripe
```

The `vendor/` directory must exist inside the container. Either:
- Run `composer install` inside the container after startup, or
- Include `vendor/` in your image build

## Environment variables

For automated setup (e.g. via a setup script), the plugin reads settings from `plugin_settings` DB table. You can populate these from env vars in your deployment scripts:

| Env var | Plugin setting | Required |
|---|---|---|
| `OJS_STRIPE_SECRET_KEY` | `secretKey` | Yes |
| `OJS_STRIPE_PUBLISHABLE_KEY` | `publishableKey` | Yes |
| `OJS_STRIPE_WEBHOOK_SECRET` | `webhookSecret` | Recommended |
| `OJS_STRIPE_TEST_MODE` | `testMode` | No (default: 1) |

## Security

- Secret key and webhook secret are stored server-side only (never sent to browser).
- Payment status is always verified server-side via Stripe API — never trusts client-supplied data.
- Webhook signature verification prevents forged/replayed events.
- Amount and currency are verified against the OJS `QueuedPayment` record.
- Double-fulfillment is prevented (idempotent).

## License

GNU General Public License v3.0. See [LICENSE](https://www.gnu.org/licenses/gpl-3.0.html).
