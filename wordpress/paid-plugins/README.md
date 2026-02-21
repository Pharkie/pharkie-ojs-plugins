# Paid Plugins

Paid/premium WordPress plugins can't be distributed via wpackagist.
Extract them here as subdirectories, each with a minimal `composer.json`.

## Setup

For each paid plugin:

1. Download the plugin zip from the vendor (WooCommerce.com, etc.)
2. Extract into a subdirectory here, e.g. `paid-plugins/woocommerce-subscriptions/`
3. Add a minimal `composer.json` in the plugin directory:

```json
{
  "name": "sea/woocommerce-subscriptions",
  "type": "wordpress-plugin",
  "version": "8.4.0"
}
```

4. Run `composer update` from the `wordpress/` directory

## Required plugins

| Plugin | Directory name | Version |
|--------|---------------|---------|
| WooCommerce Subscriptions | `woocommerce-subscriptions` | 8.4.0 |
| WooCommerce Memberships | `woocommerce-memberships` | 1.27.5 |
| Ultimate Member - WooCommerce | `um-woocommerce` | latest |

## Important

- These directories are gitignored (they contain proprietary code)
- Each team member must download and extract their own copies
- Keep versions consistent across dev/staging — check `composer.json` for expected versions
