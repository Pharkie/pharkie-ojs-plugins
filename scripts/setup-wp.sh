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
set -eo pipefail

SAMPLE_DATA=false
for arg in "$@"; do
  case "$arg" in
    --with-sample-data) SAMPLE_DATA=true ;;
  esac
done

# --- Helpers ---

# Suppress PHP notices from WP-CLI output. WP 6.7+ emits harmless
# _load_textdomain_just_in_time notices on every invocation when
# woocommerce-memberships is active. These flood the log and hide real errors.
# Filter them out so actual errors are visible.
wp_quiet() {
  local TMPOUT
  TMPOUT=$(mktemp)
  wp "$@" --allow-root >"$TMPOUT" 2>&1
  local RC=$?
  grep -v -E '_load_textdomain_just_in_time|^Deprecated:' "$TMPOUT" || true
  rm -f "$TMPOUT"
  return $RC
}

# Run a WP-CLI command with retries. For commands that depend on WC being
# fully bootstrapped — they can fail intermittently during first-run setup.
wp_retry() {
  local MAX_ATTEMPTS=3 ATTEMPT=1
  while [ "$ATTEMPT" -le "$MAX_ATTEMPTS" ]; do
    local TMPOUT
    TMPOUT=$(mktemp)
    wp "$@" --allow-root >"$TMPOUT" 2>&1
    local RC=$?
    grep -v -E '_load_textdomain_just_in_time|^Deprecated:' "$TMPOUT" || true
    rm -f "$TMPOUT"
    if [ "$RC" = "0" ]; then
      return 0
    fi
    echo "  [retry $ATTEMPT/$MAX_ATTEMPTS] Command failed (exit $RC): wp $*"
    ATTEMPT=$((ATTEMPT + 1))
    sleep 2
  done
  echo "ERROR: Command failed after $MAX_ATTEMPTS attempts: wp $*"
  return 1
}

# --- Composer install (Bedrock: download WP core + plugins) ---
# Run on first install OR when lock file has changed (new packages added).
COMPOSER_DIR="/var/www/html"
LOCK_HASH_FILE="$COMPOSER_DIR/vendor/.composer-lock-hash"
CURRENT_HASH=$(md5sum "$COMPOSER_DIR/composer.lock" 2>/dev/null | cut -d' ' -f1)
STORED_HASH=$(cat "$LOCK_HASH_FILE" 2>/dev/null || true)
if [ -f "$COMPOSER_DIR/composer.json" ] && { [ ! -d "$COMPOSER_DIR/web/wp/wp-includes" ] || [ "$CURRENT_HASH" != "$STORED_HASH" ]; }; then
  echo "Running composer install..."
  composer install --no-dev --no-interaction --working-dir="$COMPOSER_DIR" 2>&1
  echo "$CURRENT_HASH" > "$LOCK_HASH_FILE"
  echo "[ok] Composer install complete."
fi

# --- Core install ---
if ! wp core is-installed --allow-root 2>/dev/null; then
  if [ -z "${WP_ADMIN_PASSWORD}" ]; then
    echo "ERROR: WP_ADMIN_PASSWORD not set. Set it in .env — no default passwords allowed."
    exit 1
  fi
  wp core install \
    --url="${WP_HOME}" \
    --title="${WP_SITE_TITLE:-My Community}" \
    --admin_user=admin \
    --admin_password="${WP_ADMIN_PASSWORD}" \
    --admin_email=admin@example.com \
    --skip-email \
    --allow-root
fi

# --- Copy custom themes from staging area (if present) ---
# Themes live in wordpress/themes/ (gitignored) and need to be in the Bedrock
# theme path (web/app/themes/) at runtime. Deploy.sh handles this for staging/prod
# via rsync; for local dev the themes dir is already mounted, just needs copying.
THEME_STAGING="/var/www/html/themes"
THEME_DEST="/var/www/html/web/app/themes"
if [ -d "$THEME_STAGING" ]; then
  for THEME_DIR in "$THEME_STAGING"/*/; do
    [ -d "$THEME_DIR" ] || continue          # no matches → skip
    THEME_NAME=$(basename "$THEME_DIR")
    [ "$THEME_NAME" = "README.md" ] && continue
    if [ ! -d "$THEME_DEST/$THEME_NAME" ]; then
      cp -r "$THEME_DIR" "$THEME_DEST/$THEME_NAME"
      echo "[ok] Theme copied: $THEME_NAME"
    fi
  done
fi

# --- Bedrock wp-content compatibility symlink ---
# Gantry5's SCSS compiler hardcodes wp-content/themes/ in generated CSS URLs.
# Bedrock serves content from web/app/ not web/wp-content/. Create a symlink
# so /wp-content/themes/… URLs resolve to /app/themes/… on the web server.
WEB_ROOT="/var/www/html/web"
if [ ! -e "$WEB_ROOT/wp-content" ]; then
  ln -sfn "$WEB_ROOT/app" "$WEB_ROOT/wp-content"
  echo "[ok] wp-content -> app symlink created."
fi

# Activate the SEAcomm theme if available, otherwise fall back to twentytwentyfive
if wp theme is-installed seacomm --allow-root 2>/dev/null; then
  wp theme activate seacomm --allow-root 2>/dev/null
  echo "[ok] SEAcomm theme activated."
elif ! wp theme is-installed twentytwentyfive --allow-root 2>/dev/null; then
  wp theme install twentytwentyfive --activate --allow-root
fi

# --- Plugin activation ---
# Activate each plugin separately so failures are visible.
# "already active" is fine (exit 0); genuine errors (missing plugin, PHP fatal) must fail loud.
REQUIRED_PLUGINS=(
  gantry5
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
    wp_quiet plugin activate "$PLUGIN"
  fi
done

# Gantry5 writes compiled CSS into the theme and cache dirs at runtime.
# Apache runs as www-data but theme files are root-owned from rsync/cp.
# Fix permissions and clear stale cache to prevent 500 errors.
if [ -d /var/www/html/web/app/themes/seacomm/custom ]; then
  chown -R www-data:www-data /var/www/html/web/app/themes/seacomm/custom/css-compiled/ 2>/dev/null || true
  chown -R www-data:www-data /var/www/html/web/app/themes/g5_helium/custom/css-compiled/ 2>/dev/null || true
fi
mkdir -p /var/www/html/web/app/cache/gantry5
chown -R www-data:www-data /var/www/html/web/app/cache/gantry5
rm -rf /var/www/html/web/app/cache/gantry5/* 2>/dev/null || true

# --- WooCommerce readiness gate ---
# After activation, WC defers DB table creation and option seeding to the next
# page load (or CLI invocation). Verify it actually ran before proceeding.
# Without this, dependent commands (wp wc hpos, WCS seed scripts) can fail
# intermittently with "too early" / "table not found" errors.
echo "Checking WooCommerce readiness..."
for i in $(seq 1 15); do
  WC_DB_VER=$(wp option get woocommerce_db_version --allow-root 2>/dev/null) || true
  if [ -n "$WC_DB_VER" ]; then
    echo "[ok] WooCommerce ready (DB version: $WC_DB_VER)."
    break
  fi
  if [ "$i" = "1" ]; then
    echo "WooCommerce DB not initialized yet, triggering install..."
    # Force WC to run its install routine (creates tables, seeds options).
    wp eval 'if (class_exists("WC_Install")) { WC_Install::install(); }' --allow-root 2>/dev/null || true
  fi
  if [ "$i" = "15" ]; then
    echo "ERROR: WooCommerce DB version not set after 30s — WC install may have failed."
    exit 1
  fi
  sleep 2
done

# Verify WC core tables exist (the install routine should have created them).
WC_TABLE_COUNT=$(wp eval 'global $wpdb; echo $wpdb->get_var("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name LIKE \"{$wpdb->prefix}woocommerce%\"");' --allow-root 2>&1 | grep -v -E '_load_textdomain|^Deprecated:' | tr -d '[:space:]') || true
if [ -z "$WC_TABLE_COUNT" ] || [ "$WC_TABLE_COUNT" -lt "5" ]; then
  echo "WARNING: Expected >=5 WooCommerce tables, found ${WC_TABLE_COUNT:-0}. Forcing WC install..."
  wp eval 'WC_Install::install();' --allow-root 2>/dev/null || true
  sleep 2
fi

# --- WooCommerce settings ---
wp_quiet option update woocommerce_currency GBP
wp_quiet option update woocommerce_default_country GB

# WP-OJS sync settings — set defaults if not already configured
wp option get wpojs_url --allow-root 2>/dev/null || \
  wp_quiet option update wpojs_url "${WPOJS_BASE_URL}"

# Permalink structure (required for REST API)
# rewrite structure already flushes, no separate flush needed.
wp_quiet rewrite structure '/%postname%/'

# --- Suppress noisy notices ---
# WooCommerce Memberships triggers _load_textdomain_just_in_time on every
# WP-CLI call (WP 6.7+). A mu-plugin silences it at the WP level so all
# CLI commands (including wp ojs-sync) get clean output.
MU_DIR="/var/www/html/web/app/mu-plugins"
mkdir -p "$MU_DIR"
cat > "$MU_DIR/suppress-textdomain-notice.php" <<'MUEOF'
<?php
// Silence _load_textdomain_just_in_time notices from WooCommerce Memberships.
// This is a third-party plugin bug (loads translations before init), harmless
// but floods CLI output. Only suppresses this specific notice, not all.
add_filter('doing_it_wrong_trigger_error', function ($trigger, $function_name) {
    if ($function_name === '_load_textdomain_just_in_time') {
        return false;
    }
    return $trigger;
}, 10, 2);
MUEOF

# Create UM core pages (suppresses "needs to create pages" notice)
wp_quiet eval-file /var/www/html/scripts/create-um-pages.php

# Dismiss UM license + exif notices, WC onboarding/store notices
wp_quiet eval-file /var/www/html/scripts/dismiss-notices.php

# Create navigation menus and static front page (matches live site layout)
wp_quiet eval-file /var/www/html/scripts/setup-theme-content.php

echo "[ok] WordPress base setup complete."

# --- Sample data (dev/staging only) ---
if [ "$SAMPLE_DATA" = true ]; then
  CSV="/data/test-users.csv"
  if [ ! -f "$CSV" ]; then
    echo "ERROR: Sample data CSV not found at $CSV"
    echo "Run docker/anonymize-users.py on the host first to generate it."
    exit 1
  fi

  EXPECTED_USERS=$(tail -n +2 "$CSV" | wc -l | tr -d ' ')
  echo "Sample data: expecting $EXPECTED_USERS users from CSV."

  # Check if users already imported (idempotent check: look for user_0001)
  if wp user get user_0001 --allow-root 2>/dev/null; then
    echo "Sample users already imported, skipping."
  else
    echo "Importing anonymised test users from $CSV..."
    # CSV has 'role' (safe WP role) + 'original_role' (UM/WCS role).
    # wp user import-csv ignores unknown columns, so original_role is skipped.
    # Capture full output so we can check for errors, show progress.
    IMPORT_LOG=$(mktemp)
    wp user import-csv "$CSV" --allow-root 2>&1 | tee "$IMPORT_LOG" | awk '
      /^Success:/ { count++; if (count % 200 == 0) { printf "  ...imported %d/%d users\n", count, 1418; fflush() } }
      END { printf "  ...imported %d/%d users (done)\n", count, count; fflush() }'
    IMPORT_EXIT=${PIPESTATUS[0]}
    if [ "$IMPORT_EXIT" != "0" ]; then
      echo "ERROR: User import failed (exit $IMPORT_EXIT). Last 20 lines:"
      tail -20 "$IMPORT_LOG"
      rm -f "$IMPORT_LOG"
      exit 1
    fi
    if grep -qi "^error\|^fatal\|PHP Fatal" "$IMPORT_LOG" 2>/dev/null; then
      echo "ERROR: User import had errors:"
      grep -i "^error\|^fatal\|PHP Fatal" "$IMPORT_LOG" | head -10
      rm -f "$IMPORT_LOG"
      exit 1
    fi
    rm -f "$IMPORT_LOG"

    # Validate: check actual user count matches expected
    # +1 for admin user
    ACTUAL_USERS=$(wp user list --format=count --allow-root 2>/dev/null) || true
    EXPECTED_TOTAL=$((EXPECTED_USERS + 1))
    if [ -n "$ACTUAL_USERS" ] && [ "$ACTUAL_USERS" -lt "$EXPECTED_TOTAL" ]; then
      echo "WARNING: Expected $EXPECTED_TOTAL users (${EXPECTED_USERS} imported + admin), got $ACTUAL_USERS."
    else
      echo "[ok] User import complete: $ACTUAL_USERS users."
    fi

    # Disable HPOS before seeding — the seeder writes raw SQL to wp_posts.
    # If HPOS is active, WC ignores wp_posts and the subscriptions are invisible.
    # We disable it, seed, then sync and re-enable below.
    echo "Disabling HPOS for seeding..."
    wp_quiet option update woocommerce_custom_orders_table_enabled no
    wp_quiet option update woocommerce_custom_orders_table_data_sync_enabled no

    # Apply original roles via direct DB update (fast — ~2s vs ~10min with wp user set-role).
    # This is test data seeding only — production members already exist in WP.
    # wp user import-csv can't assign UM/WCS roles (validates before UM registers them),
    # so we imported as 'subscriber' and now update wp_usermeta directly via PHP.
    echo "Applying original roles (UM/WCS)..."
    wp_quiet eval-file /var/www/html/scripts/apply-roles.php "$CSV"

    echo "Seeding sample data and plugin config..."
    wp_quiet eval-file /var/www/html/scripts/setup-and-sample-data.php "$CSV"

    # Validate: check subscription count (use wp eval for reliable cross-version DB access)
    SUB_COUNT=$(wp eval 'global $wpdb; echo $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->posts} WHERE post_type=\"shop_subscription\"");' --allow-root 2>&1 | grep -v -E '_load_textdomain|^Deprecated:' | tr -d '[:space:]') || true
    if [ -z "$SUB_COUNT" ] || [ "$SUB_COUNT" = "0" ]; then
      echo "ERROR: No subscriptions found after seeding (got: '${SUB_COUNT}')."
      exit 1
    fi
    echo "[ok] $SUB_COUNT subscriptions seeded."

    # Validate: check products and type mapping were saved
    PRODUCT_COUNT=$(wp eval 'global $wpdb; echo $wpdb->get_var("SELECT COUNT(*) FROM {$wpdb->posts} WHERE post_type=\"product\" AND post_status=\"publish\"");' --allow-root 2>&1 | grep -v -E '_load_textdomain|^Deprecated:' | tr -d '[:space:]') || true
    if [ -z "$PRODUCT_COUNT" ] || [ "$PRODUCT_COUNT" = "0" ]; then
      echo "ERROR: No subscription products found after seeding (got: '${PRODUCT_COUNT}')."
      exit 1
    fi
    MAPPING_COUNT=$(wp eval 'echo count(get_option("wpojs_type_mapping", []));' --allow-root 2>&1 | grep -v -E '_load_textdomain|^Deprecated:' | tr -d '[:space:]') || true
    if [ -z "$MAPPING_COUNT" ] || [ "$MAPPING_COUNT" = "0" ]; then
      echo "ERROR: wpojs_type_mapping not set after seeding."
      exit 1
    fi
    echo "[ok] $PRODUCT_COUNT products, $MAPPING_COUNT type mappings configured."

    # Sync seeded subscriptions to HPOS (High-Performance Order Storage).
    # We disabled HPOS before seeding (raw SQL into wp_posts). Now re-enable
    # sync tracking so WC detects the un-synced posts, then sync them across.
    echo "Syncing orders to HPOS..."
    wp_quiet option update woocommerce_custom_orders_table_data_sync_enabled yes
    wp_retry wc hpos sync
    wp_retry wc hpos enable

    # Validate: HPOS is actually enabled
    HPOS_STATUS=$(wp option get woocommerce_custom_orders_table_enabled --allow-root 2>/dev/null) || true
    if [ "$HPOS_STATUS" = "yes" ]; then
      echo "[ok] HPOS enabled."
    else
      echo "WARNING: HPOS may not be enabled (status: ${HPOS_STATUS:-unknown})."
    fi

    TOTAL=$(wp user list --format=count --allow-root 2>/dev/null) || true
    echo "[ok] Sample data loaded. Total WP users: $TOTAL"
  fi
fi

# --- Final health check ---
echo ""
echo "--- Health check ---"
HEALTH_FAIL=0

# WordPress responds (301/302 are normal — Bedrock redirects to canonical URL)
WP_HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:80/ 2>/dev/null) || true
if [ "$WP_HTTP" = "200" ] || [ "$WP_HTTP" = "301" ] || [ "$WP_HTTP" = "302" ]; then
  echo "[ok] WordPress HTTP: $WP_HTTP"
else
  echo "[FAIL] WordPress HTTP: ${WP_HTTP:-timeout}"
  HEALTH_FAIL=1
fi

# WP REST API responds
REST_HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:80/wp-json/wp/v2/posts 2>/dev/null) || true
if [ "$REST_HTTP" = "200" ]; then
  echo "[ok] WP REST API: $REST_HTTP"
else
  echo "[FAIL] WP REST API: ${REST_HTTP:-timeout}"
  HEALTH_FAIL=1
fi

# WP-OJS sync plugin active
if wp plugin is-active wpojs-sync --allow-root 2>/dev/null; then
  echo "[ok] wpojs-sync plugin active."
else
  echo "[FAIL] wpojs-sync plugin not active."
  HEALTH_FAIL=1
fi

# All required plugins active
ALL_ACTIVE=true
for PLUGIN in gantry5 woocommerce woocommerce-subscriptions woocommerce-memberships ultimate-member wpojs-sync; do
  if ! wp plugin is-active "$PLUGIN" --allow-root 2>/dev/null; then
    echo "[FAIL] Plugin not active: $PLUGIN"
    ALL_ACTIVE=false
    HEALTH_FAIL=1
  fi
done
if [ "$ALL_ACTIVE" = true ]; then
  echo "[ok] All required plugins active."
fi

if [ "$HEALTH_FAIL" = "1" ]; then
  echo ""
  echo "WARNING: Health check had failures — setup may be incomplete."
  exit 1
fi

echo ""
echo "[ok] WordPress setup complete and healthy."
