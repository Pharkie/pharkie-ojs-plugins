#!/bin/bash
# Bootstrap OJS: create journal, subscription type, enable plugin.
# Idempotent — safe to run repeatedly.
# Run after OJS install: docker compose exec ojs bash /scripts/setup-ojs.sh
set -e

MARIADB="mariadb --skip-ssl -h${OJS_DB_HOST} -u${OJS_DB_USER} -p${OJS_DB_PASSWORD} ${OJS_DB_NAME}"

echo "[SEA] Setting up OJS..."

# --- Enable admin API key for scripted access ---
API_KEY_ENABLED=$($MARIADB -N -e "SELECT setting_value FROM user_settings WHERE user_id=1 AND setting_name='apiKeyEnabled'")
if [ "$API_KEY_ENABLED" != "1" ]; then
  echo "[SEA] Enabling API key for admin user..."

  # Generate a random API key
  API_KEY=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 32)

  $MARIADB <<SQL
    INSERT INTO user_settings (user_id, locale, setting_name, setting_value)
    VALUES (1, '', 'apiKeyEnabled', '1')
    ON DUPLICATE KEY UPDATE setting_value='1';
    INSERT INTO user_settings (user_id, locale, setting_name, setting_value)
    VALUES (1, '', 'apiKey', '$API_KEY')
    ON DUPLICATE KEY UPDATE setting_value='$API_KEY';
SQL
else
  API_KEY=$($MARIADB -N -e "SELECT setting_value FROM user_settings WHERE user_id=1 AND setting_name='apiKey'")
fi

# Build JWT token: header.payload.signature using the api_key_secret from config
API_SECRET=$(grep "api_key_secret" /var/www/html/config.inc.php | sed 's/.*= *//')
JWT_HEADER=$(echo -n '{"typ":"JWT","alg":"HS256"}' | base64 | tr -d '=' | tr '/+' '_-' | tr -d '\n')
JWT_PAYLOAD=$(echo -n "[\"$API_KEY\"]" | base64 | tr -d '=' | tr '/+' '_-' | tr -d '\n')
JWT_SIGNATURE=$(echo -n "${JWT_HEADER}.${JWT_PAYLOAD}" | openssl dgst -sha256 -hmac "$API_SECRET" -binary | base64 | tr -d '=' | tr '/+' '_-' | tr -d '\n')
JWT_TOKEN="${JWT_HEADER}.${JWT_PAYLOAD}.${JWT_SIGNATURE}"

# Helper function for authenticated API calls
ojs_api() {
  local METHOD=$1 URL=$2 DATA=$3
  curl -s -X "$METHOD" "http://localhost:80${URL}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $JWT_TOKEN" \
    ${DATA:+-d "$DATA"}
}

# --- Journal creation via API ---
JOURNAL_EXISTS=$($MARIADB -N -e "SELECT COUNT(*) FROM journals WHERE path='t1'")

if [ "$JOURNAL_EXISTS" = "0" ]; then
  echo "[SEA] Creating journal 't1' via API..."

  RESULT=$(ojs_api POST "/index/api/v1/contexts" '{
    "urlPath": "t1",
    "name": {"en": "Existential Analysis"},
    "primaryLocale": "en",
    "supportedLocales": ["en"],
    "supportedSubmissionLocales": ["en"],
    "enabled": true
  }')

  if echo "$RESULT" | grep -q '"urlPath":"t1"'; then
    echo "[SEA] Journal 't1' created."
  else
    echo "[SEA] WARNING: Journal creation may have failed:"
    echo "$RESULT" | head -5
  fi
else
  echo "[SEA] Journal 't1' already exists, skipping."
fi

# --- Subscription type ---
SUB_TYPE_EXISTS=$($MARIADB -N -e "SELECT COUNT(*) FROM subscription_types WHERE journal_id=1")

if [ "$SUB_TYPE_EXISTS" = "0" ]; then
  echo "[SEA] Creating subscription type..."
  $MARIADB <<'SQL'
    INSERT INTO subscription_types (journal_id, cost, currency_code_alpha, duration, format, institutional, membership, disable_public_display, seq)
    VALUES (1, 0.00, 'GBP', 365, 1, 0, 0, 1, 1);
SQL
  echo "[SEA] Subscription type created."
else
  echo "[SEA] Subscription type already exists, skipping."
fi

# --- Enable SEA plugin ---
JOURNAL_ID=$($MARIADB -N -e "SELECT journal_id FROM journals WHERE path='t1'")
echo "[SEA] Enabling sea-subscription-api plugin for journal $JOURNAL_ID..."
$MARIADB <<SQL
  INSERT IGNORE INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('seasubscriptionapiplugin', $JOURNAL_ID, 'enabled', '1', 'bool');
SQL

echo "[SEA] OJS setup complete."
