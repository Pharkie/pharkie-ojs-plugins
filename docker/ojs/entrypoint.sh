#!/bin/bash
# OJS config templating + automated install.
# Generates config.inc.php from environment variables, then hands off to PKP startup.
# If OJS is not yet installed, runs the install wizard via curl in the background.
set -e

CONFIG=/var/www/html/config.inc.php
TEMPLATE=/etc/ojs/config.inc.php.tmpl

# Generate app_key if not set (OJS 3.5 / Laravel encryption requirement)
if [ -z "$OJS_APP_KEY" ]; then
  export OJS_APP_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)
fi

# Only the variables we actually use in the template — envsubst replaces ALL ${} by
# default, which blanks out anything not in the environment.
VARS='$OJS_APP_KEY $OJS_BASE_URL $OJS_TIMEZONE $OJS_DB_HOST $OJS_DB_USER $OJS_DB_PASSWORD $OJS_DB_NAME'
VARS="$VARS "'$OJS_API_KEY_SECRET $OJS_MAIL_FROM $OJS_SMTP_ENABLED $OJS_SMTP_HOST'
VARS="$VARS "'$OJS_SMTP_PORT $OJS_SMTP_AUTH $OJS_SMTP_USER $OJS_SMTP_PASSWORD $WPOJS_ALLOWED_IPS'
VARS="$VARS "'$WPOJS_WP_MEMBER_URL $WPOJS_SUPPORT_EMAIL'
# UI messages are stored in plugin_settings (DB), not config.inc.php.
# See setup-ojs.sh for how instance-specific defaults are written.

# Check the DATABASE to determine if OJS is already installed.
# Don't trust the config file — it gets overwritten by the template on every start.
NEEDS_INSTALL=true
if mysql --skip-ssl -h "$OJS_DB_HOST" -u "$OJS_DB_USER" -p"$OJS_DB_PASSWORD" "$OJS_DB_NAME" \
    -e "SELECT 1 FROM journals LIMIT 1" &>/dev/null; then
  NEEDS_INSTALL=false
fi

# Install Stripe vendor deps (built into image, copied to bind-mounted plugin dir)
if [ -d /opt/stripe-vendor ] && [ -d /var/www/html/plugins/paymethod/stripe ]; then
  if [ ! -f /var/www/html/plugins/paymethod/stripe/vendor/autoload.php ]; then
    cp -r /opt/stripe-vendor /var/www/html/plugins/paymethod/stripe/vendor
    echo "[OJS] Stripe vendor deps installed."
  fi
fi

# Always re-template config from environment (picks up SMTP, API key, URL changes on restart)
echo "[OJS] Generating config.inc.php from template..."
envsubst "$VARS" < "$TEMPLATE" > "$CONFIG"
# Template has "installed = Off". If DB says OJS is installed, flip it to On.
if [ "$NEEDS_INSTALL" = false ]; then
  sed -i 's/^installed = Off/installed = On/' "$CONFIG"
  echo "[OJS] Config re-templated (installed = On, DB already has data)."
else
  echo "[OJS] Fresh install — config templated (installed = Off)."
fi
# Behind a reverse proxy (Caddy) that terminates SSL, Apache sees HTTP.
# OJS checks $_SERVER['HTTPS'] to decide protocol for asset URLs.
# Set HTTPS=on in Apache when X-Forwarded-Proto says https.
if echo "$OJS_BASE_URL" | grep -q '^https://'; then
  cat > /etc/apache2/conf-enabled/sea-https-proxy.conf <<'APACHE'
# Trust X-Forwarded-Proto from reverse proxy (Caddy).
# Sets HTTPS env var so PHP's $_SERVER['HTTPS'] = 'on'.
SetEnvIf X-Forwarded-Proto "https" HTTPS=on
APACHE
  echo "[OJS] Reverse proxy HTTPS detection enabled."
fi
chown www-data:www-data "$CONFIG" 2>/dev/null || true
chmod 640 "$CONFIG"

# Auto-install in background (after Apache starts)
if [ "$NEEDS_INSTALL" = true ]; then
  (
    echo "[OJS] Waiting for Apache to start..."
    for i in $(seq 1 30); do
      if curl -sf -o /dev/null http://localhost:80/index/en/install 2>/dev/null; then
        break
      fi
      sleep 2
    done

    echo "[OJS] Running OJS install..."
    RESULT=$(curl -s -L \
      -X POST "http://localhost:80/index/en/install/install" \
      --data-urlencode "installing=0" \
      --data-urlencode "locale=en" \
      --data-urlencode "additionalLocales[]=en" \
      --data-urlencode "filesDir=/var/www/files" \
      --data-urlencode "adminUsername=${OJS_ADMIN_USER:?OJS_ADMIN_USER not set}" \
      --data-urlencode "adminPassword=${OJS_ADMIN_PASSWORD:?OJS_ADMIN_PASSWORD not set}" \
      --data-urlencode "adminPassword2=${OJS_ADMIN_PASSWORD}" \
      --data-urlencode "adminEmail=${OJS_ADMIN_EMAIL:?OJS_ADMIN_EMAIL not set}" \
      --data-urlencode "databaseDriver=mysqli" \
      --data-urlencode "databaseHost=${OJS_DB_HOST}" \
      --data-urlencode "databaseUsername=${OJS_DB_USER}" \
      --data-urlencode "databasePassword=${OJS_DB_PASSWORD}" \
      --data-urlencode "databaseName=${OJS_DB_NAME}" \
      --data-urlencode "oaiRepositoryId=ojs2.localhost" \
      --data-urlencode "enableBeacon=0" \
      --data-urlencode "timeZone=${OJS_TIMEZONE:?OJS_TIMEZONE not set}" \
      2>/dev/null)

    if echo "$RESULT" | grep -q "Installation of OJS has completed successfully"; then
      echo "[OJS] OJS install complete."
    else
      echo "[OJS] WARNING: OJS install may have failed. Check logs or run manually."
    fi
  ) &
fi

# Set up scheduled tasks cron (OJS uses cron, not a persistent job worker).
# The base PKP image has cron commented out in pkp-start; we start it ourselves.
# Cron doesn't inherit Docker env vars — pass heartbeat URL inline
CRON_LINE="0 * * * *   BETTERSTACK_HB_OJS_CRON=${BETTERSTACK_HB_OJS_CRON:-} /usr/local/bin/ojs-scheduler-heartbeat.sh"
# Create wrapper that distinguishes OJS 3.5 known fatal from real failures
cat > /usr/local/bin/ojs-scheduler-heartbeat.sh <<'WRAPPER'
#!/bin/bash
OUTPUT=$(/usr/local/bin/php /var/www/html/lib/pkp/tools/scheduler.php run 2>&1)
EXIT=$?
HB="${BETTERSTACK_HB_OJS_CRON:-}"
if [ -z "$HB" ]; then exit $EXIT; fi
if [ $EXIT -eq 0 ]; then
  curl -sf "$HB" > /dev/null 2>&1
elif echo "$OUTPUT" | grep -q "NotFoundHttpException"; then
  # Known OJS 3.5 bug: tasks completed but fatal thrown after (no HTTP context)
  curl -sf "$HB" > /dev/null 2>&1
else
  # Real failure
  curl -sf -d "scheduler exit $EXIT" "$HB/fail" > /dev/null 2>&1
fi
WRAPPER
chmod +x /usr/local/bin/ojs-scheduler-heartbeat.sh
echo "$CRON_LINE" | crontab -
cron
echo "[OJS] Cron started: $(crontab -l)"
# Ping heartbeat on startup to prevent false alerts after container restart
if [ -n "$BETTERSTACK_HB_OJS_CRON" ]; then
  curl -sf "$BETTERSTACK_HB_OJS_CRON" > /dev/null && echo "[OJS] Heartbeat pinged." || true
fi

# Hand off to PKP's own startup (generates SSL certs, starts Apache)
exec /usr/local/bin/pkp-start
