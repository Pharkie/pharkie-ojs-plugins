#!/bin/bash
# Bootstrap WordPress: install core, activate plugins, set critical options.
# Idempotent — safe to run repeatedly.
#
# Usage:
#   scripts/setup-wp.sh                    # Base setup only
#   scripts/setup-wp.sh --with-sample-data # Base setup + import ~1400 test users + seed subscriptions
#
# Run inside the WP container:
#   docker compose exec wp bash /var/www/html/scripts/setup-wp.sh [--with-sample-data]
set -e

SAMPLE_DATA=false
for arg in "$@"; do
  case "$arg" in
    --with-sample-data) SAMPLE_DATA=true ;;
  esac
done

# Install core if not already (DB healthcheck handled by docker-compose depends_on)
if ! wp core is-installed --allow-root 2>/dev/null; then
  wp core install \
    --url="${WP_HOME}" \
    --title="${WP_SITE_TITLE:-My Community}" \
    --admin_user=admin \
    --admin_password="${WP_ADMIN_PASSWORD:-admin123}" \
    --admin_email=admin@example.com \
    --skip-email \
    --allow-root
fi

# Ensure a theme is installed (Bedrock doesn't bundle themes)
if ! wp theme is-installed twentytwentyfive --allow-root 2>/dev/null; then
  wp theme install twentytwentyfive --activate --allow-root
fi

# Activate plugins — each separately so failures are visible.
# "already active" is fine (exit 0); genuine errors (missing plugin, PHP fatal) must fail loud.
REQUIRED_PLUGINS=(
  woocommerce
  woocommerce-subscriptions
  woocommerce-memberships
  ultimate-member
  um-notifications
  um-woocommerce
  wpojs-sync
)
for PLUGIN in "${REQUIRED_PLUGINS[@]}"; do
  if ! wp plugin is-active "$PLUGIN" --allow-root 2>/dev/null; then
    echo "Activating $PLUGIN..."
    wp plugin activate "$PLUGIN" --allow-root
  fi
done

# Critical WooCommerce settings
wp option update woocommerce_currency GBP --allow-root
wp option update woocommerce_default_country GB --allow-root

# WP-OJS sync settings — set defaults if not already configured
wp option get wpojs_url --allow-root 2>/dev/null || \
  wp option update wpojs_url "${WPOJS_BASE_URL}" --allow-root

# Permalink structure (required for REST API)
wp rewrite structure '/%postname%/' --allow-root
wp rewrite flush --allow-root

echo "WordPress setup complete."

# --- Sample data (dev/staging only) ---
if [ "$SAMPLE_DATA" = true ]; then
  CSV="/data/test-users.csv"
  if [ ! -f "$CSV" ]; then
    echo "ERROR: Sample data CSV not found at $CSV"
    echo "Run docker/anonymize-users.py on the host first to generate it."
    exit 1
  fi

  # Check if users already imported (idempotent check: look for user_0001)
  if wp user get user_0001 --allow-root 2>/dev/null; then
    echo "Sample users already imported, skipping."
  else
    echo "Importing anonymised test users from $CSV..."
    # CSV has 'role' (safe WP role) + 'original_role' (UM/WCS role).
    # wp user import-csv ignores unknown columns, so original_role is skipped.
    wp user import-csv "$CSV" --allow-root 2>&1 | tail -5
    echo "Import done."

    # Apply original roles via direct DB update (fast — ~2s vs ~10min with wp user set-role).
    # This is test data seeding only — production members already exist in WP.
    # wp user import-csv can't assign UM/WCS roles (validates before UM registers them),
    # so we imported as 'subscriber' and now update wp_usermeta directly via PHP.
    echo "Applying original roles (UM/WCS)..."
    wp eval-file /var/www/html/scripts/apply-roles.php "$CSV" --allow-root

    echo "Seeding WooCommerce subscriptions..."
    wp eval-file /var/www/html/scripts/seed-subscriptions.php "$CSV" --allow-root

    TOTAL=$(wp user list --format=count --allow-root 2>/dev/null)
    echo "Total WP users: $TOTAL"
  fi
fi
