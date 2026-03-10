#!/bin/bash
# Bootstrap OJS: create journal, subscription types, enable plugins, configure paywall.
# Idempotent — safe to run repeatedly.
#
# Usage:
#   scripts/setup-ojs.sh                    # Base setup only
#   scripts/setup-ojs.sh --with-sample-data # Base setup + import 2 issues / 43 articles
#
# Run after OJS install:
#   docker compose exec ojs bash /scripts/setup-ojs.sh [--with-sample-data]
set -eo pipefail

SAMPLE_DATA=false
for arg in "$@"; do
  case "$arg" in
    --with-sample-data) SAMPLE_DATA=true ;;
  esac
done

MARIADB="mariadb --skip-ssl -h${OJS_DB_HOST} -u${OJS_DB_USER} -p${OJS_DB_PASSWORD} ${OJS_DB_NAME}"

# Escape a string for safe embedding in a JSON value (handles quotes, backslashes, newlines, unicode).
# Outputs the escaped content WITHOUT surrounding quotes — the caller provides those in the template.
json_escape() {
  jq -nr --arg v "$1" '$v | tojson | .[1:-1]'
}

echo "[OJS] Setting up OJS..."

# --- Wait for OJS install to finish ---
# The entrypoint runs the install wizard in the background. HTTP may respond
# before the install completes. We check two things:
# 1. Admin user exists (users table populated)
# 2. Versions table populated (OJS can actually serve requests)
# Without both, OJS returns 500 (getVersionString() on null).
echo "[OJS] Waiting for install to complete..."
for i in $(seq 1 60); do
  ADMIN_EXISTS=$($MARIADB -N -e "SELECT COUNT(*) FROM users WHERE user_id=1" 2>/dev/null || echo "0")
  VERSIONS=$($MARIADB -N -e "SELECT COUNT(*) FROM versions" 2>/dev/null || echo "0")
  if [ "$ADMIN_EXISTS" = "1" ] && [ "$VERSIONS" -gt "0" ]; then
    break
  fi
  if [ "$i" = "60" ]; then
    echo "[OJS] ERROR: Install not complete after 120s (admin=$ADMIN_EXISTS, versions=$VERSIONS)."
    exit 1
  fi
  sleep 2
done
echo "[OJS] Install complete (admin user ready, $VERSIONS version records)."

# Clear OPcache — during install, PHP caches the bootstrap in a pre-install
# state (versions table empty → getVersionString() returns null → 500 on all
# requests). Graceful restart forces Apache to reload all PHP bytecode.
echo "[OJS] Restarting Apache to clear OPcache..."
apache2ctl graceful 2>/dev/null || true
sleep 2

# --- Enable admin API key for scripted access ---
API_KEY_ENABLED=$($MARIADB -N -e "SELECT setting_value FROM user_settings WHERE user_id=1 AND setting_name='apiKeyEnabled'")
if [ "$API_KEY_ENABLED" != "1" ]; then
  echo "[OJS] Enabling API key for admin user..."

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
API_SECRET=$(grep "api_key_secret" /var/www/html/config.inc.php | head -1 | sed 's/.*= *//')
JWT_HEADER=$(echo -n '{"typ":"JWT","alg":"HS256"}' | base64 | tr -d '=' | tr '/+' '_-' | tr -d '\n')
JWT_PAYLOAD=$(echo -n "[\"$API_KEY\"]" | base64 | tr -d '=' | tr '/+' '_-' | tr -d '\n')
JWT_SIGNATURE=$(echo -n "${JWT_HEADER}.${JWT_PAYLOAD}" | openssl dgst -sha256 -hmac "$API_SECRET" -binary | base64 | tr -d '=' | tr '/+' '_-' | tr -d '\n')
JWT_TOKEN="${JWT_HEADER}.${JWT_PAYLOAD}.${JWT_SIGNATURE}"

# --- Wait for OJS API to be fully ready ---
# After install, OJS needs time to bootstrap its Laravel service container.
# Rather than blind retries on each API call, we gate on a single readiness
# check: GET /index/api/v1/contexts must return HTTP 200. This proves the
# full stack (Apache + PHP + OJS app + service container) is operational.
echo "[OJS] Waiting for API readiness..."
for i in $(seq 1 60); do
  API_HTTP=$(curl -s -o /dev/null -w '%{http_code}' \
    http://localhost:80/index/api/v1/contexts \
    -H "Authorization: Bearer $JWT_TOKEN" 2>/dev/null)
  if [ "$API_HTTP" = "200" ]; then
    echo "[OJS] API ready."
    break
  fi
  if [ "$i" = "60" ]; then
    echo "[OJS] ERROR: API not ready after 120s (last HTTP $API_HTTP)."
    exit 1
  fi
  sleep 2
done

# Helper function for authenticated API calls.
# Returns the response body. Aborts immediately on HTTP errors — the
# readiness gate above ensures OJS is fully bootstrapped before we get here.
ojs_api() {
  local METHOD=$1 URL=$2 DATA=$3
  local HTTP_CODE BODY TMPFILE

  TMPFILE=$(mktemp)
  HTTP_CODE=$(curl -s -o "$TMPFILE" -w '%{http_code}' -X "$METHOD" "http://localhost:80${URL}" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $JWT_TOKEN" \
    ${DATA:+-d "$DATA"})
  BODY=$(cat "$TMPFILE")
  rm -f "$TMPFILE"

  if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    echo "$BODY"
    return 0
  fi

  echo "[OJS] ERROR at $(date '+%H:%M:%S'): $METHOD $URL → HTTP $HTTP_CODE" >&2
  echo "[OJS] Request data: ${DATA:-(none)}" >&2
  echo "[OJS] Response: $(echo "$BODY" | head -c 500)" >&2
  exit 1
}

# --- Journal settings (all from env — no hardcoded defaults) ---
JOURNAL_PATH="${OJS_JOURNAL_PATH:?OJS_JOURNAL_PATH not set}"
JOURNAL_NAME="${OJS_JOURNAL_NAME:?OJS_JOURNAL_NAME not set}"
JOURNAL_ACRONYM="${OJS_JOURNAL_ACRONYM:-}"
JOURNAL_CONTACT_NAME="${OJS_JOURNAL_CONTACT_NAME:?OJS_JOURNAL_CONTACT_NAME not set}"
JOURNAL_CONTACT_EMAIL="${OJS_JOURNAL_CONTACT_EMAIL:?OJS_JOURNAL_CONTACT_EMAIL not set}"
JOURNAL_PUBLISHER="${OJS_JOURNAL_PUBLISHER:-}"
JOURNAL_PUBLISHER_URL="${OJS_JOURNAL_PUBLISHER_URL:-}"
JOURNAL_PRINT_ISSN="${OJS_JOURNAL_PRINT_ISSN:-}"
JOURNAL_ONLINE_ISSN="${OJS_JOURNAL_ONLINE_ISSN:-}"
JOURNAL_COUNTRY="${OJS_JOURNAL_COUNTRY:-}"

JOURNAL_EXISTS=$($MARIADB -N -e "SELECT COUNT(*) FROM journals WHERE path='$JOURNAL_PATH'")

if [ "$JOURNAL_EXISTS" = "0" ]; then
  echo "[OJS] Creating journal '$JOURNAL_NAME' ($JOURNAL_ACRONYM) via API..."

  RESULT=$(ojs_api POST "/index/api/v1/contexts" "{
    \"urlPath\": \"$(json_escape "$JOURNAL_PATH")\",
    \"name\": {\"en\": \"$(json_escape "$JOURNAL_NAME")\"},
    \"acronym\": {\"en\": \"$(json_escape "$JOURNAL_ACRONYM")\"},
    \"primaryLocale\": \"en\",
    \"supportedLocales\": [\"en\"],
    \"supportedSubmissionLocales\": [\"en\"],
    \"contactName\": \"$(json_escape "$JOURNAL_CONTACT_NAME")\",
    \"contactEmail\": \"$(json_escape "$JOURNAL_CONTACT_EMAIL")\",
    \"enabled\": true
  }")

  if echo "$RESULT" | grep -q "\"urlPath\":\"$JOURNAL_PATH\""; then
    echo "[OJS] Journal '$JOURNAL_NAME' created."
  else
    echo "[OJS] WARNING: Journal creation may have failed:"
    echo "$RESULT" | head -5
  fi
else
  echo "[OJS] Journal '$JOURNAL_PATH' already exists."
fi

# --- Journal metadata (idempotent — always ensures correct values) ---
JOURNAL_ID_META=$($MARIADB -N -e "SELECT journal_id FROM journals WHERE path='$JOURNAL_PATH'")
echo "[OJS] Configuring journal metadata..."

# Use a single API PUT to set all journal-level settings.
# Fields reference: /lib/pkp/schemas/context.json + /schemas/context.json
# About text and subscription info come from env; description/about can be overridden.
JOURNAL_DESCRIPTION="${OJS_JOURNAL_DESCRIPTION:-}"
JOURNAL_ABOUT="${OJS_JOURNAL_ABOUT:-}"
JOURNAL_SUBSCRIPTION_INFO="${OJS_JOURNAL_SUBSCRIPTION_INFO:-}"

# Build metadata JSON — use jq to conditionally add optional fields (OJS rejects empty strings)
META_JSON=$(jq -n \
  --arg name "$JOURNAL_NAME" \
  --arg acronym "$JOURNAL_ACRONYM" \
  --arg desc "$JOURNAL_DESCRIPTION" \
  --arg about "$JOURNAL_ABOUT" \
  --arg contactName "$JOURNAL_CONTACT_NAME" \
  --arg contactEmail "$JOURNAL_CONTACT_EMAIL" \
  --arg publisher "$JOURNAL_PUBLISHER" \
  --arg publisherUrl "$JOURNAL_PUBLISHER_URL" \
  --arg country "$JOURNAL_COUNTRY" \
  --arg printIssn "$JOURNAL_PRINT_ISSN" \
  --arg onlineIssn "$JOURNAL_ONLINE_ISSN" \
  --arg subInfo "$JOURNAL_SUBSCRIPTION_INFO" \
  '{
    name: {en: $name},
    acronym: {en: $acronym},
    description: {en: $desc},
    about: {en: $about},
    contactName: $contactName,
    contactEmail: $contactEmail,
    contactAffiliation: {en: $publisher},
    supportName: $contactName,
    supportEmail: $contactEmail,
    publisherInstitution: $publisher,
    publisherUrl: $publisherUrl,
    copyrightHolderType: "context",
    subscriptionName: ($name + " Subscriptions"),
    subscriptionEmail: $contactEmail,
    subscriptionAdditionalInformation: {en: $subInfo}
  }
  + if $country != "" then {country: $country} else {} end
  + if $printIssn != "" then {printIssn: $printIssn} else {} end
  + if $onlineIssn != "" then {onlineIssn: $onlineIssn} else {} end
  ')
META_RESULT=$(ojs_api PUT "/$JOURNAL_PATH/api/v1/contexts/$JOURNAL_ID_META" "$META_JSON")

# Verify the PUT succeeded (check a required field is in the response)
if ! echo "$META_RESULT" | grep -q '"publisherInstitution"'; then
  echo "[OJS] ERROR: Metadata PUT may have failed — publisherInstitution missing from response." >&2
  echo "[OJS] Response (first 500 chars): $(echo "$META_RESULT" | head -c 500)" >&2
  exit 1
fi

echo "[OJS] Journal metadata configured."

# --- Subscription types ---
# Defined via OJS_SUB_TYPES env var: pipe-separated entries of "name:cost" in GBP.
# Example: "UK Membership:50|Student Membership:35|International Membership:60"
# duration = NULL means non-expiring. OJS validates subscriptions by checking
# (st.duration IS NULL OR (checkDate >= s.date_start AND checkDate <= s.date_end)).
JOURNAL_ID_SUB=$($MARIADB -N -e "SELECT journal_id FROM journals WHERE path='$JOURNAL_PATH'")
SUB_TYPE_COUNT=$($MARIADB -N -e "SELECT COUNT(*) FROM subscription_types WHERE journal_id=$JOURNAL_ID_SUB")

OJS_SUB_TYPES="${OJS_SUB_TYPES:-}"

if [ "$SUB_TYPE_COUNT" = "0" ] && [ -n "$OJS_SUB_TYPES" ]; then
  echo "[OJS] Creating subscription types..."
  SEQ=1
  IFS='|' read -ra TYPES <<< "$OJS_SUB_TYPES"
  for TYPE_DEF in "${TYPES[@]}"; do
    TYPE_NAME="${TYPE_DEF%%:*}"
    TYPE_COST="${TYPE_DEF##*:}"
    # Validate cost is numeric
    if ! [[ "$TYPE_COST" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
      echo "[OJS] ERROR: Invalid cost '$TYPE_COST' for subscription type '$TYPE_NAME'" >&2
      exit 1
    fi
    # Escape backslashes and single quotes for SQL
    TYPE_NAME_SQL=$(printf '%s' "$TYPE_NAME" | sed "s/\\\\/\\\\\\\\/g; s/'/''/g")
    echo "[OJS]   $TYPE_NAME (£$TYPE_COST)"
    $MARIADB -e "INSERT INTO subscription_types (journal_id, cost, currency_code_alpha, duration, format, institutional, membership, disable_public_display, seq) VALUES ($JOURNAL_ID_SUB, $TYPE_COST, 'GBP', NULL, 1, 0, 0, 0, $SEQ)"
    TYPE_ID=$($MARIADB -N -e "SELECT type_id FROM subscription_types WHERE journal_id=$JOURNAL_ID_SUB AND seq=$SEQ")
    $MARIADB -e "INSERT INTO subscription_type_settings (type_id, locale, setting_name, setting_value, setting_type) VALUES ($TYPE_ID, 'en', 'name', '$TYPE_NAME_SQL', 'string')"
    SEQ=$((SEQ + 1))
  done
  echo "[OJS] Subscription types created."
elif [ "$SUB_TYPE_COUNT" = "0" ]; then
  echo "[OJS] WARNING: No subscription types created (OJS_SUB_TYPES not set)."
else
  # Fix existing types if they have a duration set (breaks non-expiring subscriptions).
  WRONG_DURATION=$($MARIADB -N -e "SELECT COUNT(*) FROM subscription_types WHERE journal_id=$JOURNAL_ID_SUB AND duration IS NOT NULL")
  if [ "$WRONG_DURATION" -gt "0" ]; then
    echo "[OJS] Fixing subscription type duration (NULL = non-expiring)..."
    $MARIADB -e "UPDATE subscription_types SET duration = NULL WHERE journal_id = $JOURNAL_ID_SUB"
  fi
  echo "[OJS] Subscription types already exist ($SUB_TYPE_COUNT type(s)), skipping."
fi

# --- Enable WP-OJS plugin ---
JOURNAL_ID=$($MARIADB -N -e "SELECT journal_id FROM journals WHERE path='$JOURNAL_PATH'")
echo "[OJS] Enabling wpojs-subscription-api plugin for journal $JOURNAL_ID..."
$MARIADB <<SQL
  INSERT IGNORE INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('wpojssubscriptionapiplugin', $JOURNAL_ID, 'enabled', '1', 'bool');
SQL

# --- UI messages (stored in plugin_settings, not config.inc.php) ---
# PHP INI files corrupt values containing " and {} (HTML with href="..." and
# {placeholder}), so we write instance-specific messages directly to the DB.
# These pre-populate the Settings form. Admins can further edit via the UI.
# Generic defaults are in the PHP plugin constants if env vars are not set.
LOGIN_HINT="${WPOJS_DEFAULT_LOGIN_HINT:-}"
PW_RESET_HINT="${WPOJS_DEFAULT_PASSWORD_RESET_HINT:-}"
PAYWALL_HINT="${WPOJS_DEFAULT_PAYWALL_HINT:-}"
FOOTER_MSG="${WPOJS_DEFAULT_FOOTER_MESSAGE:-}"

if [ -n "$LOGIN_HINT" ] || [ -n "$PW_RESET_HINT" ] || [ -n "$PAYWALL_HINT" ] || [ -n "$FOOTER_MSG" ]; then
  echo "[OJS] Writing UI messages to plugin settings..."
  # Escape single quotes for SQL safety
  sql_escape() { printf '%s' "$1" | sed "s/'/''/g"; }

  [ -n "$LOGIN_HINT" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('wpojssubscriptionapiplugin', $JOURNAL_ID, 'loginHint', '$(sql_escape "$LOGIN_HINT")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape "$LOGIN_HINT")';"
  [ -n "$PW_RESET_HINT" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('wpojssubscriptionapiplugin', $JOURNAL_ID, 'passwordResetHint', '$(sql_escape "$PW_RESET_HINT")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape "$PW_RESET_HINT")';"
  [ -n "$PAYWALL_HINT" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('wpojssubscriptionapiplugin', $JOURNAL_ID, 'paywallHint', '$(sql_escape "$PAYWALL_HINT")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape "$PAYWALL_HINT")';"
  [ -n "$FOOTER_MSG" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('wpojssubscriptionapiplugin', $JOURNAL_ID, 'footerMessage', '$(sql_escape "$FOOTER_MSG")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape "$FOOTER_MSG")';"
  echo "[OJS] UI messages written."
fi

# --- Enable subscription (paywall) mode ---
# publishingMode: 0 = open access, 1 = subscription, 2 = none.
# OJS won't enforce the paywall without this.
PUB_MODE=$($MARIADB -N -e "SELECT setting_value FROM journal_settings WHERE journal_id=$JOURNAL_ID AND setting_name='publishingMode'" 2>/dev/null)

if [ "$PUB_MODE" != "1" ]; then
  echo "[OJS] Setting publishing mode to 'Subscription'..."
  ojs_api PUT "/$JOURNAL_PATH/api/v1/contexts/$JOURNAL_ID" '{"publishingMode": 1}' > /dev/null
  echo "[OJS] Publishing mode set."
else
  echo "[OJS] Publishing mode already set to 'Subscription', skipping."
fi

# --- Enable payments + PayPal plugin ---
# Payments must be enabled for the paywall to work. The PayPal plugin handles
# non-member purchases (single articles, issues, back issues).
PAYMENTS_ENABLED=$($MARIADB -N -e "SELECT setting_value FROM journal_settings WHERE journal_id=$JOURNAL_ID AND setting_name='paymentsEnabled'" 2>/dev/null)

ARTICLE_FEE="${OJS_PURCHASE_ARTICLE_FEE:-0}"
ISSUE_FEE="${OJS_PURCHASE_ISSUE_FEE:-0}"

if [ "$PAYMENTS_ENABLED" != "1" ]; then
  echo "[OJS] Enabling payments (article £$ARTICLE_FEE, issue £$ISSUE_FEE)..."
  ojs_api PUT "/$JOURNAL_PATH/api/v1/contexts/$JOURNAL_ID" "{
    \"paymentsEnabled\": true,
    \"paymentPluginName\": \"PaypalPayment\",
    \"currency\": \"GBP\",
    \"purchaseArticleFee\": $ARTICLE_FEE,
    \"purchaseArticleFeeEnabled\": true,
    \"purchaseIssueFee\": $ISSUE_FEE,
    \"purchaseIssueFeeEnabled\": true,
    \"membershipFee\": 0
  }" > /dev/null
  echo "[OJS] Payments enabled."
else
  echo "[OJS] Payments already enabled, skipping."
fi

# Enable PayPal payment plugin + test mode
PAYPAL_ENABLED=$($MARIADB -N -e "SELECT COUNT(*) FROM plugin_settings WHERE plugin_name='paypalpayment' AND context_id=$JOURNAL_ID AND setting_name='enabled' AND setting_value='1'" 2>/dev/null)
if [ "$PAYPAL_ENABLED" = "0" ]; then
  echo "[OJS] Enabling PayPal payment plugin (test mode)..."
  $MARIADB <<SQL
    INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
    VALUES ('paypalpayment', $JOURNAL_ID, 'enabled', '1', 'bool')
    ON DUPLICATE KEY UPDATE setting_value='1';
    INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
    VALUES ('paypalpayment', $JOURNAL_ID, 'testMode', '1', 'bool')
    ON DUPLICATE KEY UPDATE setting_value='1';
SQL
  echo "[OJS] PayPal plugin enabled (test mode)."
else
  echo "[OJS] PayPal plugin already enabled, skipping."
fi

# --- DOI configuration ---
DOI_PREFIX="${OJS_DOI_PREFIX:-}"
if [ -n "$DOI_PREFIX" ]; then
  DOI_ENABLED=$($MARIADB -N -e "SELECT setting_value FROM journal_settings WHERE journal_id=$JOURNAL_ID AND setting_name='doiPrefix'" 2>/dev/null)
  if [ "$DOI_ENABLED" != "$DOI_PREFIX" ]; then
    echo "[OJS] Configuring DOIs (prefix: $DOI_PREFIX)..."
    # Enable DOIs, set prefix, assign to articles + issues, suffix = default, assignment = never
    $MARIADB <<SQL
      INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID, '', 'enableDois', '1')
      ON DUPLICATE KEY UPDATE setting_value='1';
      INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID, '', 'doiPrefix', '$DOI_PREFIX')
      ON DUPLICATE KEY UPDATE setting_value='$DOI_PREFIX';
      INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID, '', 'enabledDoiTypes', '["publication","issue"]')
      ON DUPLICATE KEY UPDATE setting_value='["publication","issue"]';
      INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID, '', 'doiSuffixType', 'default')
      ON DUPLICATE KEY UPDATE setting_value='default';
      INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID, '', 'doiCreationTime', 'never')
      ON DUPLICATE KEY UPDATE setting_value='never';
      INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID, '', 'doiVersioning', '0')
      ON DUPLICATE KEY UPDATE setting_value='0';
      INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID, '', 'registrationAgency', 'crossrefplugin')
      ON DUPLICATE KEY UPDATE setting_value='crossrefplugin';
SQL
    echo "[OJS] DOIs configured."
  else
    echo "[OJS] DOIs already configured (prefix: $DOI_PREFIX), skipping."
  fi

  # Crossref plugin settings (depositor info from env, credentials optional)
  DEPOSITOR_NAME="${OJS_DOI_DEPOSITOR_NAME:-}"
  DEPOSITOR_EMAIL="${OJS_DOI_DEPOSITOR_EMAIL:-}"
  CROSSREF_USER="${OJS_CROSSREF_USERNAME:-}"
  CROSSREF_PASS="${OJS_CROSSREF_PASSWORD:-}"

  if [ -n "$DEPOSITOR_NAME" ] || [ -n "$DEPOSITOR_EMAIL" ]; then
    echo "[OJS] Configuring Crossref depositor..."
    sql_escape_doi() { printf '%s' "$1" | sed "s/'/''/g"; }

    # Enable the Crossref plugin
    $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('crossrefplugin', $JOURNAL_ID, 'enabled', '1', 'bool') ON DUPLICATE KEY UPDATE setting_value='1';"

    [ -n "$DEPOSITOR_NAME" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('crossrefplugin', $JOURNAL_ID, 'depositorName', '$(sql_escape_doi "$DEPOSITOR_NAME")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape_doi "$DEPOSITOR_NAME")';"
    [ -n "$DEPOSITOR_EMAIL" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('crossrefplugin', $JOURNAL_ID, 'depositorEmail', '$(sql_escape_doi "$DEPOSITOR_EMAIL")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape_doi "$DEPOSITOR_EMAIL")';"
    [ -n "$CROSSREF_USER" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('crossrefplugin', $JOURNAL_ID, 'username', '$(sql_escape_doi "$CROSSREF_USER")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape_doi "$CROSSREF_USER")';"
    [ -n "$CROSSREF_PASS" ] && $MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type) VALUES ('crossrefplugin', $JOURNAL_ID, 'password', '$(sql_escape_doi "$CROSSREF_PASS")', 'string') ON DUPLICATE KEY UPDATE setting_value='$(sql_escape_doi "$CROSSREF_PASS")';"

    echo "[OJS] Crossref depositor configured."
  fi
else
  echo "[OJS] DOI prefix not set (OJS_DOI_PREFIX), skipping DOI config."
fi

echo "[OJS] OJS base setup complete."

# --- Sample data (dev/staging only) ---
if [ "$SAMPLE_DATA" = true ]; then
  IMPORT_XML="/data/ojs-import-clean.xml"
  if [ ! -f "$IMPORT_XML" ]; then
    echo "[OJS] WARNING: Import XML not found at $IMPORT_XML — skipping content import."
    echo "[OJS] To import: scp the file to the VPS 'data export/' directory and re-run setup."
  else

  # Idempotent check: see if articles already exist
  ARTICLE_COUNT=$($MARIADB -N -e "SELECT COUNT(*) FROM publications WHERE submission_id > 0")
  if [ "$ARTICLE_COUNT" -gt "0" ]; then
    echo "[OJS] Articles already imported ($ARTICLE_COUNT publications), skipping."
  else
    echo "[OJS] Importing OJS content (2 issues, 43 articles)..."
    IMPORT_OUTPUT=$(php /var/www/html/tools/importExport.php NativeImportExportPlugin import "$IMPORT_XML" "$JOURNAL_PATH" 2>&1)
    IMPORT_EXIT=$?
    # Show the import summary (skip PHP Notices which are harmless tempnam warnings)
    echo "$IMPORT_OUTPUT" | grep -v "PHP Notice" | tail -10
    if [ "$IMPORT_EXIT" != "0" ]; then
      echo "[OJS] ERROR: Import exited with code $IMPORT_EXIT"
      exit 1
    fi
    NEW_COUNT=$($MARIADB -N -e "SELECT COUNT(*) FROM publications WHERE submission_id > 0")
    if [ "$NEW_COUNT" = "0" ]; then
      echo "[OJS] ERROR: Import reported success but no publications found in DB."
      exit 1
    fi
    echo "[OJS] [ok] Import complete. Publications: $NEW_COUNT"
  fi

  # Set issues to require subscription (access_status: 1 = open, 2 = subscription).
  # Without this, articles stay open access even when the journal is in subscription mode.
  # Runs every time (not just on fresh import) so re-running the script fixes issues that
  # were imported before paywall mode was configured.
  OPEN_ISSUES=$($MARIADB -N -e "SELECT COUNT(*) FROM issues WHERE journal_id=$JOURNAL_ID AND access_status != 2")
  if [ "$OPEN_ISSUES" -gt "0" ]; then
    echo "[OJS] Setting $OPEN_ISSUES issue(s) to require subscription..."
    $MARIADB -e "UPDATE issues SET access_status = 2 WHERE journal_id=$JOURNAL_ID"
    echo "[OJS] Issue access updated."
  fi
  fi
fi

# --- Final health check ---
echo ""
echo "[OJS] --- Health check ---"
HEALTH_FAIL=0

# OJS HTTP responds
OJS_HTTP=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:80/ 2>/dev/null) || true
if [ "$OJS_HTTP" = "200" ] || [ "$OJS_HTTP" = "302" ]; then
  echo "[OJS] [ok] HTTP: $OJS_HTTP"
else
  echo "[OJS] [FAIL] HTTP: ${OJS_HTTP:-timeout}"
  HEALTH_FAIL=1
fi

# Journal API responds
JOURNAL_HTTP=$(curl -s -o /dev/null -w '%{http_code}' \
  "http://localhost:80/$JOURNAL_PATH/api/v1/contexts/$JOURNAL_ID" \
  -H "Authorization: Bearer $JWT_TOKEN" 2>/dev/null) || true
if [ "$JOURNAL_HTTP" = "200" ]; then
  echo "[OJS] [ok] Journal API: $JOURNAL_HTTP"
else
  echo "[OJS] [FAIL] Journal API: ${JOURNAL_HTTP:-timeout}"
  HEALTH_FAIL=1
fi

# WP-OJS plugin enabled in DB
PLUGIN_OK=$($MARIADB -N -e "SELECT COUNT(*) FROM plugin_settings WHERE plugin_name='wpojssubscriptionapiplugin' AND setting_name='enabled' AND setting_value='1'" 2>/dev/null) || true
if [ "$PLUGIN_OK" = "1" ]; then
  echo "[OJS] [ok] wpojs-subscription-api plugin enabled."
else
  echo "[OJS] [FAIL] wpojs-subscription-api plugin not enabled in DB."
  HEALTH_FAIL=1
fi

# Subscription types exist
SUB_TYPES=$($MARIADB -N -e "SELECT COUNT(*) FROM subscription_types WHERE journal_id=$JOURNAL_ID" 2>/dev/null) || true
if [ -n "$SUB_TYPES" ] && [ "$SUB_TYPES" -gt "0" ]; then
  echo "[OJS] [ok] $SUB_TYPES subscription type(s)."
else
  echo "[OJS] [FAIL] No subscription types found."
  HEALTH_FAIL=1
fi

# Publishing mode = subscription
PUB_CHECK=$($MARIADB -N -e "SELECT setting_value FROM journal_settings WHERE journal_id=$JOURNAL_ID AND setting_name='publishingMode'" 2>/dev/null) || true
if [ "$PUB_CHECK" = "1" ]; then
  echo "[OJS] [ok] Publishing mode: subscription."
else
  echo "[OJS] [FAIL] Publishing mode: ${PUB_CHECK:-not set} (expected 1)."
  HEALTH_FAIL=1
fi

# DOI prefix configured (if set in env)
if [ -n "${OJS_DOI_PREFIX:-}" ]; then
  DOI_CHECK=$($MARIADB -N -e "SELECT setting_value FROM journal_settings WHERE journal_id=$JOURNAL_ID AND setting_name='doiPrefix'" 2>/dev/null) || true
  if [ "$DOI_CHECK" = "$OJS_DOI_PREFIX" ]; then
    echo "[OJS] [ok] DOI prefix: $DOI_CHECK"
  else
    echo "[OJS] [FAIL] DOI prefix: ${DOI_CHECK:-not set} (expected $OJS_DOI_PREFIX)."
    HEALTH_FAIL=1
  fi
fi

if [ "$HEALTH_FAIL" = "1" ]; then
  echo ""
  echo "[OJS] WARNING: Health check had failures — setup may be incomplete."
  exit 1
fi

echo ""
echo "[OJS] [ok] OJS setup complete and healthy."
