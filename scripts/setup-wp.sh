#!/bin/bash
# Bootstrap WordPress: install core, activate plugins, set critical options.
# Idempotent — safe to run repeatedly.
# Run inside the WP container: docker compose exec wp bash /var/www/html/scripts/setup-wp.sh
set -e

# Install core if not already (DB healthcheck handled by docker-compose depends_on)
if ! wp core is-installed --allow-root 2>/dev/null; then
  wp core install \
    --url="${WP_HOME}" \
    --title="SEA Community" \
    --admin_user=admin \
    --admin_password="${WP_ADMIN_PASSWORD:-admin123}" \
    --admin_email=admin@example.com \
    --skip-email \
    --allow-root
fi

# Activate plugins (idempotent — already-active plugins are skipped)
# Paid plugins (woocommerce-subscriptions, woocommerce-memberships, um-woocommerce)
# are not yet available — add them here once the zips are obtained.
wp plugin activate \
  woocommerce \
  ultimate-member \
  sea-ojs-sync \
  --allow-root 2>/dev/null || true

# Critical WooCommerce settings
wp option update woocommerce_currency GBP --allow-root
wp option update woocommerce_default_country GB --allow-root

# SEA OJS sync settings — set defaults if not already configured
wp option get sea_ojs_base_url --allow-root 2>/dev/null || \
  wp option update sea_ojs_base_url "${SEA_OJS_BASE_URL}" --allow-root

# Permalink structure (required for REST API)
wp rewrite structure '/%postname%/' --allow-root
wp rewrite flush --allow-root

echo "WordPress setup complete."
