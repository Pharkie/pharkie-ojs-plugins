#!/bin/bash
# Assign editorial roles to OJS users from a JSON mapping file.
#
# Reads docs/private/editorial-roles.json (bind-mounted to /data/ in container) and for each entry:
#   1. Finds user by email (or creates with random password)
#   2. Assigns specified OJS user group(s)
#   3. Sets masthead flag for Editorial Masthead page
#
# Idempotent — safe to re-run.
#
# Usage (inside OJS container, called by setup.sh):
#   bash /scripts/assign-roles.sh
#
# Usage (standalone via docker exec):
#   $DC exec -T ojs bash /scripts/assign-roles.sh
set -eo pipefail

ROLES_FILE="/data/editorial-roles.json"
if [ ! -f "$ROLES_FILE" ]; then
  echo "[Roles] No editorial-roles.json found at $ROLES_FILE — skipping."
  exit 0
fi

# DB connection (same pattern as setup-ojs.sh)
DB_HOST="${OJS_DB_HOST:?ERROR: OJS_DB_HOST is not set}"
DB_USER="${OJS_DB_USER:?ERROR: OJS_DB_USER is not set}"
DB_PASS="${OJS_DB_PASSWORD:?ERROR: OJS_DB_PASSWORD is not set}"
DB_NAME="${OJS_DB_NAME:?ERROR: OJS_DB_NAME is not set}"
MARIADB="mysql --skip-ssl -h $DB_HOST -u $DB_USER -p$DB_PASS $DB_NAME"

JOURNAL_ID=$($MARIADB -N -e "SELECT journal_id FROM journals LIMIT 1")
if [ -z "$JOURNAL_ID" ]; then
  echo "[Roles] ERROR: No journal found. Run setup-ojs.sh first."
  exit 1
fi

echo "[Roles] Assigning editorial roles (journal_id=$JOURNAL_ID)..."

# Parse JSON with jq, process each entry
COUNT=$(jq length "$ROLES_FILE")
for i in $(seq 0 $((COUNT - 1))); do
  EMAIL=$(jq -r ".[$i].email" "$ROLES_FILE")
  FIRST=$(jq -r ".[$i].firstName" "$ROLES_FILE")
  LAST=$(jq -r ".[$i].lastName" "$ROLES_FILE")
  MASTHEAD=$(jq -r "if .[$i].masthead then \"1\" else \"0\" end" "$ROLES_FILE")
  ROLE_COUNT=$(jq ".[$i].roles | length" "$ROLES_FILE")

  # Find user by email
  USER_ID=$($MARIADB -N -e "SELECT user_id FROM users WHERE email='$EMAIL' LIMIT 1")

  if [ -z "$USER_ID" ]; then
    # Create user with random bcrypt password (via PHP, available in OJS container)
    RANDOM_PASS=$(php -r 'echo password_hash(bin2hex(random_bytes(16)), PASSWORD_BCRYPT, ["cost"=>12]);')

    # Generate username from name (lowercase, alphanumeric)
    USERNAME=$(echo "${FIRST}${LAST}" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')
    # Handle collision
    EXISTING=$($MARIADB -N -e "SELECT COUNT(*) FROM users WHERE username='$USERNAME'")
    if [ "$EXISTING" != "0" ]; then
      USERNAME="${USERNAME}$(shuf -i 100-999 -n 1)"
    fi

    $MARIADB -e "INSERT INTO users (username, password, email, date_registered, must_change_password, disabled, date_validated)
      VALUES ('$USERNAME', '$RANDOM_PASS', '$EMAIL', NOW(), 1, 0, NOW());"
    USER_ID=$($MARIADB -N -e "SELECT user_id FROM users WHERE email='$EMAIL' LIMIT 1")

    # Set name
    $MARIADB -e "INSERT INTO user_settings (user_id, setting_name, setting_value, locale) VALUES
      ($USER_ID, 'givenName', '$FIRST', 'en'),
      ($USER_ID, 'familyName', '$LAST', 'en')
      ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);"

    echo "[Roles]   Created $EMAIL ($FIRST $LAST) as $USERNAME (user_id=$USER_ID, must_change_password=1)"
  else
    echo "[Roles]   Found $EMAIL (user_id=$USER_ID)"
  fi

  # Additive role assignment — never delete existing roles.
  # Each role in the JSON is ensured to exist; roles not in the JSON are left untouched.
  for j in $(seq 0 $((ROLE_COUNT - 1))); do
    ROLE_NAME=$(jq -r ".[$i].roles[$j]" "$ROLES_FILE")

    # "Site admin" is a site-level role (context_id IS NULL), not a journal-level role.
    if [ "$ROLE_NAME" = "Site admin" ]; then
      GROUP_ID=$($MARIADB -N -e "SELECT user_group_id FROM user_groups WHERE role_id = 1 LIMIT 1")
    else
      GROUP_ID=$($MARIADB -N -e "SELECT ug.user_group_id FROM user_groups ug
        JOIN user_group_settings ugs ON ug.user_group_id = ugs.user_group_id
        WHERE ugs.setting_name='name' AND ugs.setting_value='$ROLE_NAME' AND ugs.locale='en'
        AND ug.context_id = $JOURNAL_ID LIMIT 1")
    fi

    if [ -z "$GROUP_ID" ]; then
      echo "[Roles]   WARNING: Role '$ROLE_NAME' not found, skipping."
      continue
    fi

    # Insert with masthead flag (idempotent)
    EXISTING=$($MARIADB -N -e "SELECT COUNT(*) FROM user_user_groups WHERE user_group_id=$GROUP_ID AND user_id=$USER_ID")
    if [ "$EXISTING" = "0" ]; then
      $MARIADB -e "INSERT INTO user_user_groups (user_group_id, user_id, masthead) VALUES ($GROUP_ID, $USER_ID, $MASTHEAD);"
      echo "[Roles]     + $ROLE_NAME (group $GROUP_ID, masthead=$MASTHEAD)"
    else
      # Update masthead flag if needed
      $MARIADB -e "UPDATE user_user_groups SET masthead=$MASTHEAD WHERE user_group_id=$GROUP_ID AND user_id=$USER_ID;"
      echo "[Roles]     = $ROLE_NAME (already assigned, masthead=$MASTHEAD)"
    fi
  done
done

echo "[Roles] Done."
