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
VARS="$VARS "'$WPOJS_API_KEY_SECRET $OJS_MAIL_FROM $OJS_SMTP_ENABLED $OJS_SMTP_HOST'
VARS="$VARS "'$OJS_SMTP_PORT $OJS_SMTP_AUTH $OJS_SMTP_USER $OJS_SMTP_PASSWORD $WPOJS_ALLOWED_IPS'
VARS="$VARS "'$WPOJS_WP_MEMBER_URL $WPOJS_SUPPORT_EMAIL'
VARS="$VARS "'$WPOJS_DEFAULT_LOGIN_HINT $WPOJS_DEFAULT_PAYWALL_HINT $WPOJS_DEFAULT_FOOTER_MESSAGE'

# Default UI messages (used if not set in .env)
export WPOJS_DEFAULT_LOGIN_HINT="${WPOJS_DEFAULT_LOGIN_HINT:-Member? First time here? <a href=\"{lostPasswordUrl}\">Set your password</a> to access journal content.}"
export WPOJS_DEFAULT_PAYWALL_HINT="${WPOJS_DEFAULT_PAYWALL_HINT:-If you believe you should have access through your membership, please contact <a href=\"mailto:{supportEmail}\">{supportEmail}</a>.}"
export WPOJS_DEFAULT_FOOTER_MESSAGE="${WPOJS_DEFAULT_FOOTER_MESSAGE:-Your journal access is provided by your membership. <a href=\"{wpUrl}\">Manage your membership</a>.}"

NEEDS_INSTALL=false

# Generate config from template if missing, empty, or not yet installed
if [ ! -s "$CONFIG" ] || grep -q "installed = Off" "$CONFIG"; then
  echo "[OJS] Generating config.inc.php from template..."
  envsubst "$VARS" < "$TEMPLATE" > "$CONFIG"
  chown www-data:www-data "$CONFIG" 2>/dev/null || true
  chmod 640 "$CONFIG"
  NEEDS_INSTALL=true
fi

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
      --data-urlencode "adminUsername=${OJS_ADMIN_USER:-admin}" \
      --data-urlencode "adminPassword=${OJS_ADMIN_PASSWORD:-admin123}" \
      --data-urlencode "adminPassword2=${OJS_ADMIN_PASSWORD:-admin123}" \
      --data-urlencode "adminEmail=${OJS_ADMIN_EMAIL:-admin@example.com}" \
      --data-urlencode "databaseDriver=mysqli" \
      --data-urlencode "databaseHost=${OJS_DB_HOST}" \
      --data-urlencode "databaseUsername=${OJS_DB_USER}" \
      --data-urlencode "databasePassword=${OJS_DB_PASSWORD}" \
      --data-urlencode "databaseName=${OJS_DB_NAME}" \
      --data-urlencode "oaiRepositoryId=ojs2.localhost" \
      --data-urlencode "enableBeacon=0" \
      --data-urlencode "timeZone=${OJS_TIMEZONE:-Europe/London}" \
      2>/dev/null)

    if echo "$RESULT" | grep -q "Installation of OJS has completed successfully"; then
      echo "[OJS] OJS install complete."
    else
      echo "[OJS] WARNING: OJS install may have failed. Check logs or run manually."
    fi
  ) &
fi

# Hand off to PKP's own startup (generates SSL certs, starts Apache)
exec /usr/local/bin/pkp-start
