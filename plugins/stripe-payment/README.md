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

The plugin stores separate test and live key pairs. The **Test Mode** checkbox in the admin UI switches which keys are used at runtime. When enabled, the plugin uses test keys — no real charges are made. See the [Test mode](#test-mode) section below for setup details.

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

The `vendor/` directory (Stripe PHP SDK) must exist inside the container. It is **not** checked into git — run `composer install --no-dev` inside the container after deploy:

```bash
docker compose exec ojs bash -c 'cd /var/www/html/plugins/paymethod/stripe && \
  curl -sS https://getcomposer.org/installer | php -- --install-dir=/tmp && \
  /tmp/composer.phar install --no-dev'
```

Or include the `composer install` step in your image build.

## Environment variables

The plugin itself reads settings from the `plugin_settings` DB table at runtime — **not** from environment variables. These env vars are consumed by the deployment setup script (`setup-ojs.sh`) which writes them into `plugin_settings`:

| Env var | Plugin setting | Required |
|---|---|---|
| `OJS_STRIPE_SECRET_KEY` | `secretKey` | Yes (live) |
| `OJS_STRIPE_PUBLISHABLE_KEY` | `publishableKey` | Yes (live) |
| `OJS_STRIPE_TEST_SECRET_KEY` | `testSecretKey` | Yes (test) |
| `OJS_STRIPE_TEST_PUBLISHABLE_KEY` | `testPublishableKey` | Yes (test) |
| `OJS_STRIPE_WEBHOOK_SECRET` | `webhookSecret` | Recommended |
| `OJS_STRIPE_TEST_MODE` | `testMode` | No (default: 0) |

You can also configure settings manually via the OJS admin UI (Settings → Distribution → Payments).

## Test mode

When "Test Mode" is enabled in the OJS admin UI, the plugin uses the test keys instead of live keys. No real charges are made.

### Setup

1. In the Stripe dashboard, toggle to **Test mode** (top right)
2. Go to **Developers → API keys**:
   - Create a **restricted key** with only **Checkout Sessions → Write**
   - Copy the restricted key (`rk_test_...`) and the publishable key (`pk_test_...`)
3. Go to **Workbench → Event destinations → Add destination**:
   - URL: `https://your-ojs-domain/index.php/{journal}/payment/plugin/StripePayment/webhook`
   - Event: `checkout.session.completed` only
   - Copy the signing secret (`whsec_...`) — this is separate from the live webhook secret
4. Enter the test keys in OJS admin (Distribution → Payments → Stripe) or in `.env`
5. Tick "Test Mode" and save

### Test card numbers

| Card | Result |
|---|---|
| `4242 4242 4242 4242` | Success |
| `4000 0000 0000 0002` | Decline |

Use any future expiry date, any CVC, any postcode.

## Security

- Secret key and webhook secret are stored server-side only (never sent to browser).
- Payment status is always verified server-side via Stripe API — never trusts client-supplied data.
- Webhook signature verification prevents forged/replayed events.
- Amount and currency are verified against the OJS `QueuedPayment` record (both redirect callback and webhook).
- Double-fulfillment is prevented (idempotent).

## Troubleshooting

- **Webhook returns 400:** Check that the `Stripe-Signature` HTTP header reaches PHP. Apache with PHP-FPM may strip custom headers — add `CGIPassAuth on` to `.htaccess` or configure Apache to pass the header.
- **"A payment error occurred":** Check OJS container logs (`docker logs <ojs-container>`) for the specific Stripe API error. Common causes: invalid secret key, expired keys, or missing `vendor/` directory.
- **Checkout Session creates but redirect fails:** Ensure `OJS_BASE_URL` in your OJS config matches the actual URL the browser uses. Mismatches (e.g. `http` vs `https`, or wrong hostname) cause Stripe's redirect to fail.

## Uninstallation

1. Switch to a different payment plugin in Settings → Distribution → Payments.
2. Remove the plugin directory and Docker bind mount.
3. Optionally clean up: `DELETE FROM versions WHERE product = 'stripe';` and `DELETE FROM plugin_settings WHERE plugin_name = 'stripepayment';`

## LLM Generated, Human Reviewed

This code was generated with Claude Code (Anthropic, Claude Opus 4.6). Development was overseen by the human author with attention to reliability and security. Architectural decisions, configuration choices, and development sessions were closely planned, directed and verified by the human author throughout. The code and test results were reviewed and tested by the human author beyond the LLM. Still, the code has had limited manual review, I encourage you to make your own checks and use this code at your own risk.

## License

PolyForm Noncommercial 1.0.0 — see [LICENSE.md](../../LICENSE.md).
