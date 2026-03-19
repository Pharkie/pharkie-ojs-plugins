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

# --- Footer (links + SEA logo, matching live site) ---
# The live site has two columns of nav links + the SEA logo on the right.
# Hide the OJS brand image (ojs_brand.png) via CSS — can't modify core templates.
JPATH="${OJS_JOURNAL_PATH:-ea}"
FOOTER_HTML="<style>.pkp_brand_footer{display:none}</style><div style=\"display:flex;justify-content:space-between;align-items:flex-start;gap:40px;\"><div style=\"display:flex;gap:40px;\"><div><a href=\"/$JPATH/issue/current\">Current</a><br><a href=\"/$JPATH/issue/archive\">Archives</a><br><a href=\"/$JPATH/about\">About</a><br><a href=\"/$JPATH/about/submissions\">Submissions</a></div><div><a href=\"/$JPATH/about/editorialMasthead\">Editorial Masthead</a><br><a href=\"/$JPATH/about/contact\">Contact</a><br><a href=\"/$JPATH/about/privacy\">Privacy Statement</a></div></div><div><img style=\"max-height:80px\" src=\"/public/site/images/sea-logo-footer.png\" alt=\"Society for Existential Analysis\" width=\"200\" height=\"87\"></div></div>"
$MARIADB -e "INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
  VALUES ($JOURNAL_ID_META, 'en', 'pageFooter', '$(echo "$FOOTER_HTML" | sed "s/'/''/g")')
  ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);"
echo "[OJS] Footer configured."

# --- Branding (logo, favicon, homepage image) ---
# Images are baked into the Docker image at /opt/ojs-branding/ (from docker/ojs/branding/).
# This copies them to OJS's public directory and sets the DB metadata so OJS serves them.
# Idempotent — skips if files already exist.
BRANDING_SRC="/opt/ojs-branding"
PUBLIC_DIR="/var/www/html/public/journals/$JOURNAL_ID_META"

if [ -d "$BRANDING_SRC" ] && [ "$(ls -A "$BRANDING_SRC" 2>/dev/null)" ]; then
  echo "[OJS] Applying branding images..."
  mkdir -p "$PUBLIC_DIR"

  # Helper: install one branding image (copies file + writes DB setting)
  # Usage: install_branding_image <setting_name> <source_file> <upload_name> <width> <height> [alt_text]
  install_branding_image() {
    local SETTING=$1 SRC_FILE=$2 UPLOAD_NAME=$3 WIDTH=$4 HEIGHT=$5 ALT_TEXT=${6:-}
    local DEST="$PUBLIC_DIR/$UPLOAD_NAME"

    if [ ! -f "$SRC_FILE" ]; then
      echo "[OJS]   Skipping $SETTING — source not found: $SRC_FILE"
      return
    fi

    # Copy file (always overwrite to pick up any updates)
    cp "$SRC_FILE" "$DEST"
    chown www-data:www-data "$DEST"

    # Build the JSON value OJS expects: {name, uploadName, width, height, dateUploaded, altText}
    local DATE_NOW
    DATE_NOW=$(date -u '+%Y-%m-%d %H:%M:%S')
    local JSON_VAL
    JSON_VAL=$(jq -n \
      --arg name "$(basename "$SRC_FILE")" \
      --arg uploadName "$UPLOAD_NAME" \
      --argjson width "$WIDTH" \
      --argjson height "$HEIGHT" \
      --arg dateUploaded "$DATE_NOW" \
      --arg altText "$ALT_TEXT" \
      '{name: $name, uploadName: $uploadName, width: $width, height: $height, dateUploaded: $dateUploaded, altText: $altText}')

    # OJS stores multilingual image settings as JSON in journal_settings (locale = 'en')
    local SQL_VAL
    SQL_VAL=$(printf '%s' "$JSON_VAL" | sed "s/'/''/g")
    $MARIADB -e "INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
      VALUES ($JOURNAL_ID_META, 'en', '$SETTING', '$SQL_VAL')
      ON DUPLICATE KEY UPDATE setting_value='$SQL_VAL';"

    echo "[OJS]   $SETTING → $UPLOAD_NAME (${WIDTH}x${HEIGHT})"
  }

  install_branding_image "pageHeaderLogoImage" \
    "$BRANDING_SRC/pageHeaderLogoImage_en.png" "pageHeaderLogoImage_en.png" 1173 511 \
    "Existential Analysis journal logo"

  install_branding_image "favicon" \
    "$BRANDING_SRC/favicon_en.png" "favicon_en.png" 771 800 ""

  install_branding_image "homepageImage" \
    "$BRANDING_SRC/homepageImage_en.png" "homepageImage_en.png" 7175 1880 \
    "Existential Analysis homepage banner"

  echo "[OJS] Branding images applied."
else
  echo "[OJS] No branding images found at $BRANDING_SRC, skipping."
fi

# --- Theme settings (default theme options to match live site) ---
OJS_THEME_COLOUR="${OJS_THEME_COLOUR:-#b91515}"
OJS_THEME_TYPOGRAPHY="${OJS_THEME_TYPOGRAPHY:-notoSans}"
OJS_THEME_SHOW_DESCRIPTION="${OJS_THEME_SHOW_DESCRIPTION:-1}"

echo "[OJS] Configuring theme (colour: $OJS_THEME_COLOUR, font: $OJS_THEME_TYPOGRAPHY)..."
$MARIADB <<SQL
  INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('defaultthemeplugin', $JOURNAL_ID_META, 'baseColour', '$OJS_THEME_COLOUR', 'string')
  ON DUPLICATE KEY UPDATE setting_value='$OJS_THEME_COLOUR';
  INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('defaultthemeplugin', $JOURNAL_ID_META, 'showDescriptionInJournalIndex', '$OJS_THEME_SHOW_DESCRIPTION', 'string')
  ON DUPLICATE KEY UPDATE setting_value='$OJS_THEME_SHOW_DESCRIPTION';
  INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('defaultthemeplugin', $JOURNAL_ID_META, 'typography', '$OJS_THEME_TYPOGRAPHY', 'string')
  ON DUPLICATE KEY UPDATE setting_value='$OJS_THEME_TYPOGRAPHY';
SQL
# Clear compiled CSS cache so theme changes take effect immediately
rm -f /var/www/html/cache/1-stylesheet-*.css /var/www/html/cache/1-font-*.css
echo "[OJS] Theme configured (CSS cache cleared)."

# --- Nav menu (flat layout matching live site) ---
# OJS 3.5 default creates nested About dropdown. We flatten it to match the live
# OJS 3.4 layout: Current | Archives | About | Submissions | Editorial Masthead | Contact
# Also removes Announcements and Privacy from primary nav.
echo "[OJS] Configuring navigation menu..."
# Get primary nav menu ID (NMI_TYPE_PRIMARY)
PRIMARY_NAV_ID=$($MARIADB -N -e "SELECT navigation_menu_id FROM navigation_menus WHERE area_name='primary' AND context_id=$JOURNAL_ID_META" 2>/dev/null)
if [ -n "$PRIMARY_NAV_ID" ]; then
  # Remove nested items from About dropdown; promote Submissions, Masthead, Contact to top-level
  # First, get the item IDs we need
  ABOUT_ID=$($MARIADB -N -e "SELECT i.navigation_menu_item_id FROM navigation_menu_items i JOIN navigation_menu_item_assignments a ON i.navigation_menu_item_id = a.navigation_menu_item_id WHERE i.type='NMI_TYPE_ABOUT' AND a.navigation_menu_id=$PRIMARY_NAV_ID AND a.parent_id IS NULL LIMIT 1" 2>/dev/null)
  if [ -n "$ABOUT_ID" ]; then
    # Delete child assignments under the About dropdown
    $MARIADB -e "DELETE FROM navigation_menu_item_assignments WHERE navigation_menu_id=$PRIMARY_NAV_ID AND parent_id=$ABOUT_ID;"

    # Remove Announcements and Privacy from nav (if present as top-level)
    $MARIADB -e "DELETE a FROM navigation_menu_item_assignments a JOIN navigation_menu_items i ON a.navigation_menu_item_id = i.navigation_menu_item_id WHERE a.navigation_menu_id=$PRIMARY_NAV_ID AND i.type IN ('NMI_TYPE_ANNOUNCEMENTS','NMI_TYPE_PRIVACY');"

    # Ensure Submissions, Masthead, Contact are top-level (parent_id = NULL)
    for TYPE in NMI_TYPE_SUBMISSIONS NMI_TYPE_MASTHEAD NMI_TYPE_CONTACT; do
      ITEM_ID=$($MARIADB -N -e "SELECT i.navigation_menu_item_id FROM navigation_menu_items i WHERE i.context_id=$JOURNAL_ID_META AND i.type='$TYPE' LIMIT 1" 2>/dev/null)
      if [ -n "$ITEM_ID" ]; then
        ASSIGNED=$($MARIADB -N -e "SELECT COUNT(*) FROM navigation_menu_item_assignments WHERE navigation_menu_id=$PRIMARY_NAV_ID AND navigation_menu_item_id=$ITEM_ID" 2>/dev/null)
        if [ "$ASSIGNED" = "0" ]; then
          MAX_SEQ=$($MARIADB -N -e "SELECT COALESCE(MAX(seq),0) FROM navigation_menu_item_assignments WHERE navigation_menu_id=$PRIMARY_NAV_ID" 2>/dev/null)
          $MARIADB -e "INSERT INTO navigation_menu_item_assignments (navigation_menu_id, navigation_menu_item_id, parent_id, seq) VALUES ($PRIMARY_NAV_ID, $ITEM_ID, NULL, $((MAX_SEQ + 1)));"
        else
          $MARIADB -e "UPDATE navigation_menu_item_assignments SET parent_id = NULL WHERE navigation_menu_id=$PRIMARY_NAV_ID AND navigation_menu_item_id=$ITEM_ID;"
        fi
      fi
    done

    # Use OJS 3.5's correct label "Editorial Masthead" (was "Editorial Team" in 3.4)
    MASTHEAD_ID=$($MARIADB -N -e "SELECT navigation_menu_item_id FROM navigation_menu_items WHERE context_id=$JOURNAL_ID_META AND type='NMI_TYPE_MASTHEAD' LIMIT 1" 2>/dev/null)
    if [ -n "$MASTHEAD_ID" ]; then
      $MARIADB -e "INSERT INTO navigation_menu_item_settings (navigation_menu_item_id, locale, setting_name, setting_value, setting_type) VALUES ($MASTHEAD_ID, 'en', 'title', 'Editorial Masthead', 'string') ON DUPLICATE KEY UPDATE setting_value='Editorial Masthead';"
    fi

    # Re-sequence: Current(0), Archives(1), About(2), Submissions(3), Masthead(4), Contact(5)
    SEQ_NUM=0
    for TYPE in NMI_TYPE_CURRENT NMI_TYPE_ARCHIVES NMI_TYPE_ABOUT NMI_TYPE_SUBMISSIONS NMI_TYPE_MASTHEAD NMI_TYPE_CONTACT; do
      ITEM_ID=$($MARIADB -N -e "SELECT i.navigation_menu_item_id FROM navigation_menu_items i JOIN navigation_menu_item_assignments a ON i.navigation_menu_item_id = a.navigation_menu_item_id WHERE i.context_id=$JOURNAL_ID_META AND i.type='$TYPE' AND a.navigation_menu_id=$PRIMARY_NAV_ID AND a.parent_id IS NULL LIMIT 1" 2>/dev/null)
      if [ -n "$ITEM_ID" ]; then
        $MARIADB -e "UPDATE navigation_menu_item_assignments SET seq=$SEQ_NUM WHERE navigation_menu_id=$PRIMARY_NAV_ID AND navigation_menu_item_id=$ITEM_ID;"
      fi
      SEQ_NUM=$((SEQ_NUM + 1))
    done
    echo "[OJS] Nav menu restructured (flat layout)."
  else
    echo "[OJS] Nav menu: About item not found, skipping restructure."
  fi
else
  echo "[OJS] Nav menu: primary nav not found, skipping."
fi

# --- Disable user registration (members are created via sync) ---
$MARIADB -e "INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
  VALUES ($JOURNAL_ID_META, '', 'disableUserReg', '1')
  ON DUPLICATE KEY UPDATE setting_value='1';"
echo "[OJS] User registration disabled."

# --- Editorial team users (for OJS 3.5 masthead page) ---
# OJS 3.5 auto-generates the masthead from user accounts assigned to editorial groups.
# Live OJS 3.4 uses a static HTML `editorialTeam` setting instead.
# We create both: user accounts for 3.5 masthead + static HTML fallback for the setting.
echo "[OJS] Setting up editorial team..."

# Create editorial team users (idempotent — skips if username exists)
create_editorial_user() {
  local USERNAME=$1 GIVEN=$2 FAMILY=$3 EMAIL=$4 GROUP_ID=$5
  local EXISTS=$($MARIADB -N -e "SELECT COUNT(*) FROM users WHERE username='$USERNAME'")
  if [ "$EXISTS" = "0" ]; then
    $MARIADB -e "INSERT INTO users (username, password, email, date_registered, must_change_password, disabled)
      VALUES ('$USERNAME', '\$2y\$10\$placeholder', '$EMAIL', NOW(), 0, 0);"
    local USER_ID=$($MARIADB -N -e "SELECT user_id FROM users WHERE username='$USERNAME'")
    $MARIADB -e "INSERT INTO user_settings (user_id, setting_name, setting_value, locale) VALUES
      ($USER_ID, 'givenName', '$GIVEN', 'en'),
      ($USER_ID, 'familyName', '$FAMILY', 'en');"
    $MARIADB -e "INSERT INTO user_user_groups (user_group_id, user_id) VALUES ($GROUP_ID, $USER_ID);"
    echo "[OJS]   Created $USERNAME ($GIVEN $FAMILY) → group $GROUP_ID"
  else
    echo "[OJS]   $USERNAME already exists, skipping."
  fi
}

# Group 3 = Journal editor, Group 5 = Section editor
create_editorial_user "martin.adams" "Martin" "Adams" "journal@existentialanalysis.org.uk" 3
create_editorial_user "simon.duplock" "Simon" "du Plock" "sduplock@existentialanalysis.org.uk" 3
create_editorial_user "ondine.smulders" "Ondine" "Smulders" "smuldersea@outlook.com" 5
create_editorial_user "richard.swann" "Richard" "Swann" "rswann@existentialanalysis.org.uk" 5

# Enable masthead page
$MARIADB -e "INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
  VALUES ($JOURNAL_ID_META, '', 'masthead', '1')
  ON DUPLICATE KEY UPDATE setting_value='1';"

# Static editorialTeam HTML (used by OJS 3.4; kept as fallback content)
if [ -f "/opt/ojs-branding/editorialTeam.html" ]; then
  EDITORIAL_HTML=$(cat /opt/ojs-branding/editorialTeam.html)
  EDITORIAL_SQL=$(printf '%s' "$EDITORIAL_HTML" | sed "s/'/''/g")
  $MARIADB -e "INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
    VALUES ($JOURNAL_ID_META, 'en', 'editorialTeam', '$EDITORIAL_SQL')
    ON DUPLICATE KEY UPDATE setting_value='$EDITORIAL_SQL';"
  echo "[OJS]   editorialTeam HTML set."
fi

# Author guidelines (submissions page content from live site)
if [ -f "/opt/ojs-branding/authorGuidelines.html" ]; then
  GUIDELINES_HTML=$(cat /opt/ojs-branding/authorGuidelines.html)
  GUIDELINES_SQL=$(printf '%s' "$GUIDELINES_HTML" | sed "s/'/''/g")
  $MARIADB -e "INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
    VALUES ($JOURNAL_ID_META, 'en', 'authorGuidelines', '$GUIDELINES_SQL')
    ON DUPLICATE KEY UPDATE setting_value='$GUIDELINES_SQL';"
  echo "[OJS]   authorGuidelines set."
fi

# Contact info (matching live site)
$MARIADB <<SQL
  UPDATE journal_settings SET setting_value='Richard Swann'
    WHERE journal_id=$JOURNAL_ID_META AND setting_name='contactName';
  UPDATE journal_settings SET setting_value='journal@existentialanalysis.org.uk'
    WHERE journal_id=$JOURNAL_ID_META AND setting_name='contactEmail';
  INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
    VALUES ($JOURNAL_ID_META, 'en', 'contactAffiliation', 'Society for Existential Analysis')
    ON DUPLICATE KEY UPDATE setting_value='Society for Existential Analysis';
  UPDATE journal_settings SET setting_value='Richard Swann'
    WHERE journal_id=$JOURNAL_ID_META AND setting_name='supportName';
  UPDATE journal_settings SET setting_value='journal@existentialanalysis.org.uk'
    WHERE journal_id=$JOURNAL_ID_META AND setting_name='supportEmail';
SQL
echo "[OJS]   Contact info configured."
echo "[OJS] Editorial team setup complete."

# --- Custom sidebar blocks (event banners from live site) ---
# Uses OJS Custom Block Manager plugin to create sidebar banners.
# Banner images are baked into Docker image at /opt/ojs-branding/.
echo "[OJS] Configuring sidebar blocks..."

# Enable Custom Block Manager plugin
$MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('customblockmanagerplugin', $JOURNAL_ID_META, 'enabled', '1', 'bool')
  ON DUPLICATE KEY UPDATE setting_value='1';"

# Register block names
$MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('customblockmanagerplugin', $JOURNAL_ID_META, 'blocks', '[\"advertisers-link\",\"banner\",\"sea-events-banner\"]', 'object')
  ON DUPLICATE KEY UPDATE setting_value='[\"advertisers-link\",\"banner\",\"sea-events-banner\"]';"

# Copy banner images to site public directory
SITE_IMG_DIR="/var/www/html/public/site/images"
mkdir -p "$SITE_IMG_DIR"
for IMG in sea-events-1.png sea-events-2.png sea-logo-footer.png; do
  if [ -f "$BRANDING_SRC/$IMG" ]; then
    cp "$BRANDING_SRC/$IMG" "$SITE_IMG_DIR/$IMG"
  fi
done
chown -R www-data:www-data "$SITE_IMG_DIR" 2>/dev/null || true

# Create block settings using PHP for correct JSON encoding
# Banner links point to WP community site (env-driven, not hardcoded)
WP_BANNER_URL="${WPOJS_WP_MEMBER_URL:-http://localhost:8080}"
php -r '
$pdo = new PDO("mysql:host='"$OJS_DB_HOST"';dbname='"$OJS_DB_NAME"'", "'"$OJS_DB_USER"'", "'"$OJS_DB_PASSWORD"'", [PDO::MYSQL_ATTR_SSL_VERIFY_SERVER_CERT => false]);
$ctx = '"$JOURNAL_ID_META"';
$wpUrl = "'"$WP_BANNER_URL"'";

$blocks = [
  "advertisers-link" => "<ul style=\"list-style:none;padding:0;margin:0\"><li><a href=\"/ea/advertisers\">For Advertisers</a></li></ul>",
  "banner" => "<a href=\"" . $wpUrl . "\"><img src=\"/public/site/images/sea-events-1.png\" alt=\"SEA Events\" style=\"max-width:100%\"></a>",
  "sea-events-banner" => "<a href=\"" . $wpUrl . "\"><img src=\"/public/site/images/sea-events-2.png\" alt=\"SEA Events\" style=\"max-width:100%\"></a>",
];

$stmt = $pdo->prepare("INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES (?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value), setting_type = VALUES(setting_type)");

foreach ($blocks as $name => $html) {
  $content = json_encode(["en" => $html]);
  $title = json_encode(["en" => ""]);
  $stmt->execute([$name, $ctx, "blockContent", $content, "object"]);
  $stmt->execute([$name, $ctx, "blockTitle", $title, "object"]);
  $stmt->execute([$name, $ctx, "enabled", "1", "bool"]);
}
echo "Custom blocks configured.\n";
'

# Configure sidebar: Information block + both custom banners
$MARIADB -e "INSERT INTO journal_settings (journal_id, locale, setting_name, setting_value)
  VALUES ($JOURNAL_ID_META, '', 'sidebar', '[\"informationblockplugin\",\"advertisers-link\",\"banner\",\"sea-events-banner\"]')
  ON DUPLICATE KEY UPDATE setting_value='[\"informationblockplugin\",\"advertisers-link\",\"banner\",\"sea-events-banner\"]';"

echo "[OJS] Sidebar blocks configured."

# --- Static Pages plugin (For Advertisers page, matching live site) ---
echo "[OJS] Configuring Static Pages plugin..."

# Enable the plugin
$MARIADB -e "INSERT INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('staticpagesplugin', $JOURNAL_ID_META, 'enabled', '1', 'bool')
  ON DUPLICATE KEY UPDATE setting_value='1';"

# Register in versions table (OJS requires this for generic plugins)
$MARIADB -e "INSERT IGNORE INTO versions (major, minor, revision, build, date_installed, current, product_type, product, product_class_name, lazy_load, sitewide)
  VALUES (1, 0, 0, 0, NOW(), 1, 'plugins.generic', 'staticPages', 'StaticPagesPlugin', 1, 0);"

# Create the advertisers static page
OJS_DB_HOST="$OJS_DB_HOST" OJS_DB_NAME="$OJS_DB_NAME" OJS_DB_USER="$OJS_DB_USER" \
OJS_DB_PASSWORD="$OJS_DB_PASSWORD" JOURNAL_ID_META="$JOURNAL_ID_META" \
php <<'PHPEOF'
<?php
$pdo = new PDO(
  "mysql:host=" . getenv("OJS_DB_HOST") . ";dbname=" . getenv("OJS_DB_NAME"),
  getenv("OJS_DB_USER"), getenv("OJS_DB_PASSWORD"),
  [PDO::MYSQL_ATTR_SSL_VERIFY_SERVER_CERT => false]
);
$ctx = (int) getenv("JOURNAL_ID_META");

// Check if page already exists
$check = $pdo->prepare("SELECT static_page_id FROM static_pages WHERE path = ? AND context_id = ?");
$check->execute(["advertisers", $ctx]);
$pageId = $check->fetchColumn();

if (!$pageId) {
    $ins = $pdo->prepare("INSERT INTO static_pages (path, context_id) VALUES (?, ?)");
    $ins->execute(["advertisers", $ctx]);
    $pageId = $pdo->lastInsertId();
}

$content = <<<'HTML'
<p>Existential Analysis reaches a specialist readership of psychotherapists, counsellors, psychologists, researchers, supervisors, trainers, and students with an interest in existential and phenomenological approaches to therapy and life.</p>

<p>We accept a limited number of advertising placements that are relevant to our community, including:</p>

<ul>
<li>Training programmes and CPD events</li>
<li>Conferences and workshops</li>
<li>Books and journals</li>
<li>Clinical services and supervision directories</li>
<li>Professional organisations and member services</li>
</ul>

<h2>Advertising options</h2>
<ul>
<li>Website placement (sidebar / homepage placement, subject to availability)</li>
<li>Issue-based placements (e.g., inside-cover or back-page for PDF/print, if applicable)</li>
<li>Sponsored announcements (clearly labelled)</li>
</ul>

<h2>Audience and fit</h2>
<p>We prioritise adverts that are aligned with the journal's ethos and likely to be of genuine interest to readers. We do not accept advertising that is misleading, sensational, or unrelated to the journal's remit.</p>

<h2>Rates and booking</h2>
<p>Rates depend on placement, duration, and format. To request the current rate card and availability, contact us with:</p>
<ul>
<li>What you want to promote</li>
<li>Preferred dates / issue (if relevant)</li>
<li>Your website link</li>
<li>Any artwork you already have (PDF/PNG/JPG)</li>
</ul>

<h2>Further Information for Issue-based Placements</h2>

<h3>Advertising rates</h3>
<ul>
<li>Full page (115mm width x 190mm height) <strong>£80</strong></li>
<li>Half page (115mm width x 90mm height) <strong>£50</strong></li>
</ul>

<p><strong>10% discount</strong> when 2 adverts are booked consecutively.</p>

<h3>Submission deadlines</h3>
<ul>
<li><strong>1st December</strong> for January issue inclusion</li>
<li><strong>1st June</strong> for July issue inclusion</li>
</ul>

<h3>Submission details</h3>
<ul>
<li>As a word document</li>
<li>By email to: <a href="mailto:journal@existentialanalysis.org.uk">journal@existentialanalysis.org.uk</a></li>
</ul>

<h2>Contact</h2>
<p>Email: <a href="mailto:journal@existentialanalysis.org.uk">journal@existentialanalysis.org.uk</a></p>
HTML;

$stmt = $pdo->prepare("INSERT INTO static_page_settings (static_page_id, locale, setting_name, setting_value, setting_type)
  VALUES (?, ?, ?, ?, ?) ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)");
$stmt->execute([$pageId, "en", "title", "For Advertisers", "string"]);
$stmt->execute([$pageId, "en", "content", $content, "string"]);
echo "Static page \"advertisers\" configured (id=$pageId).\n";
PHPEOF

echo "[OJS] Static Pages plugin configured."

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

# --- Enable Inline HTML Galley plugin ---
echo "[OJS] Enabling inline-html-galley plugin for journal $JOURNAL_ID..."
$MARIADB <<SQL
  INSERT IGNORE INTO plugin_settings (plugin_name, context_id, setting_name, setting_value, setting_type)
  VALUES ('inlinehtmlgalleyplugin', $JOURNAL_ID, 'enabled', '1', 'bool');
  INSERT IGNORE INTO versions (major, minor, revision, build, date_installed, current, product_type, product, product_class_name, lazy_load, sitewide)
  VALUES (1, 0, 0, 0, NOW(), 1, 'plugins.generic', 'inlineHtmlGalley', 'InlineHtmlGalleyPlugin', 1, 0);
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

# --- Clear caches ---
# Nav menu, sidebar, and theme changes require a full cache flush.
# OJS caches compiled templates, CSS, and DB queries aggressively.
# Note: apache2ctl restart is NOT safe inside docker exec (kills the parent process).
# Instead, we clear all cache files and do a graceful reload, which is sufficient
# because OJS recompiles templates on cache miss.
echo "[OJS] Clearing caches..."
find /var/www/html/cache/ -type f -delete 2>/dev/null || true
apache2ctl graceful 2>/dev/null || true
sleep 2
echo "[OJS] Caches cleared, Apache reloaded."

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

  # Fix section ordering to match live site: Editorial, Articles, Book Review Editorial, Book Reviews.
  # OJS import ignores seq values from XML, so all sections get seq=0 and fall back to section_id order.
  echo "[OJS] Fixing section display order..."
  $MARIADB -e "
    UPDATE sections SET seq=0 WHERE section_id=(SELECT section_id FROM (SELECT s.section_id FROM sections s JOIN section_settings ss ON s.section_id=ss.section_id WHERE ss.setting_name='title' AND ss.setting_value='Editorial') t);
    UPDATE sections SET seq=1 WHERE section_id=(SELECT section_id FROM (SELECT s.section_id FROM sections s JOIN section_settings ss ON s.section_id=ss.section_id WHERE ss.setting_name='title' AND ss.setting_value='Articles') t);
    UPDATE sections SET seq=2 WHERE section_id=(SELECT section_id FROM (SELECT s.section_id FROM sections s JOIN section_settings ss ON s.section_id=ss.section_id WHERE ss.setting_name='title' AND ss.setting_value='Book Review Editorial') t);
    UPDATE sections SET seq=3 WHERE section_id=(SELECT section_id FROM (SELECT s.section_id FROM sections s JOIN section_settings ss ON s.section_id=ss.section_id WHERE ss.setting_name='title' AND ss.setting_value='Book Reviews') t);
  "
  echo "[OJS] Section order fixed."

  # Set archive display order: newest first (by date_published DESC).
  # OJS archive page sorts by custom_issue_orders.seq, not by date_published.
  # Without this, issues display in import/insertion order (random).
  EXISTING_ORDER=$($MARIADB -N -e "SELECT COUNT(*) FROM custom_issue_orders WHERE journal_id=$JOURNAL_ID")
  if [ "$EXISTING_ORDER" = "0" ]; then
    echo "[OJS] Setting archive order (newest first)..."
    $MARIADB -e "
      INSERT INTO custom_issue_orders (issue_id, journal_id, seq)
      SELECT issue_id, journal_id, @row := @row + 1 as seq
      FROM issues, (SELECT @row := 0) r
      WHERE journal_id=$JOURNAL_ID AND published = 1
      ORDER BY date_published DESC;"
    echo "[OJS] Archive order set."
  fi

  # Set current issue to the newest (highest date_published).
  # OJS stores this in journals.current_issue_id, NOT journal_settings.
  CURRENT_ISSUE_ID=$($MARIADB -N -e "SELECT issue_id FROM issues WHERE journal_id=$JOURNAL_ID AND published=1 ORDER BY date_published DESC LIMIT 1")
  if [ -n "$CURRENT_ISSUE_ID" ]; then
    $MARIADB -e "UPDATE journals SET current_issue_id=$CURRENT_ISSUE_ID WHERE journal_id=$JOURNAL_ID;"
    echo "[OJS] Current issue set (issue_id=$CURRENT_ISSUE_ID)."
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

# Inline HTML Galley plugin enabled in DB
INLINE_OK=$($MARIADB -N -e "SELECT COUNT(*) FROM plugin_settings WHERE plugin_name='inlinehtmlgalleyplugin' AND setting_name='enabled' AND setting_value='1'" 2>/dev/null) || true
if [ "$INLINE_OK" = "1" ]; then
  echo "[OJS] [ok] inline-html-galley plugin enabled."
else
  echo "[OJS] [FAIL] inline-html-galley plugin not enabled in DB."
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

# Branding images installed
if [ -d "/opt/ojs-branding" ] && [ "$(ls -A /opt/ojs-branding 2>/dev/null)" ]; then
  LOGO_EXISTS=$($MARIADB -N -e "SELECT COUNT(*) FROM journal_settings WHERE journal_id=$JOURNAL_ID AND setting_name='pageHeaderLogoImage' AND locale='en'" 2>/dev/null) || true
  if [ "$LOGO_EXISTS" = "1" ]; then
    echo "[OJS] [ok] Branding: logo installed."
  else
    echo "[OJS] [FAIL] Branding: logo not found in DB."
    HEALTH_FAIL=1
  fi
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
