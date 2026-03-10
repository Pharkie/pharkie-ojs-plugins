#!/bin/bash
# Generate .env from .env.example with random passwords and secrets.
# Refuses to overwrite an existing .env file.
#
# Usage:
#   scripts/generate-env.sh              # Generate .env in project root
#   scripts/generate-env.sh /path/to/.env  # Generate at a specific path
set -eo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${1:-$PROJECT_ROOT/.env}"

if [ -f "$ENV_FILE" ]; then
  echo "ERROR: $ENV_FILE already exists. Remove it first if you want to regenerate."
  exit 1
fi

# Use .env.dev (project-specific defaults) if available, otherwise .env.example (generic)
ENV_TEMPLATE="$PROJECT_ROOT/.env.dev"
if [ ! -f "$ENV_TEMPLATE" ]; then
  ENV_TEMPLATE="$PROJECT_ROOT/.env.example"
fi

if [ ! -f "$ENV_TEMPLATE" ]; then
  echo "ERROR: No .env.dev or .env.example found."
  exit 1
fi

echo "Generating $ENV_FILE from $ENV_TEMPLATE..."
cp "$ENV_TEMPLATE" "$ENV_FILE"

# --- Helper: generate a random value and fill an empty variable ---
fill_empty() {
  local var="$1"
  local value="$2"
  # Match lines where VAR= is followed by nothing or only whitespace/comment
  # Only replace if the value part is empty (VAR= or VAR=   # comment)
  if grep -qE "^${var}=\s*(#.*)?$" "$ENV_FILE"; then
    # Preserve any inline comment
    sed -i "s|^${var}=\s*\(#.*\)\?$|${var}=${value}|" "$ENV_FILE"
  fi
}

# --- Generate passwords ---
DB_PASSWORD=$(openssl rand -base64 18)
WP_ADMIN_PASSWORD=$(openssl rand -base64 18)
WP_DB_ROOT_PASSWORD=$(openssl rand -base64 18)
OJS_DB_PASSWORD=$(openssl rand -base64 18)
OJS_DB_ROOT_PASSWORD=$(openssl rand -base64 18)
OJS_ADMIN_PASSWORD=$(openssl rand -base64 18)
WPOJS_API_KEY=$(openssl rand -hex 32)

fill_empty "DB_PASSWORD" "$DB_PASSWORD"
fill_empty "WP_ADMIN_PASSWORD" "$WP_ADMIN_PASSWORD"
fill_empty "WP_DB_ROOT_PASSWORD" "$WP_DB_ROOT_PASSWORD"
fill_empty "WP_DB_PASSWORD" "$DB_PASSWORD"           # Must match DB_PASSWORD
fill_empty "OJS_DB_PASSWORD" "$OJS_DB_PASSWORD"
fill_empty "OJS_DB_ROOT_PASSWORD" "$OJS_DB_ROOT_PASSWORD"
fill_empty "OJS_ADMIN_PASSWORD" "$OJS_ADMIN_PASSWORD"
fill_empty "WPOJS_API_KEY" "$WPOJS_API_KEY"
fill_empty "WPOJS_API_KEY_SECRET" "$WPOJS_API_KEY"   # Must match WPOJS_API_KEY

# --- Generate auth salts ---
for SALT in AUTH_KEY SECURE_AUTH_KEY LOGGED_IN_KEY NONCE_KEY AUTH_SALT SECURE_AUTH_SALT LOGGED_IN_SALT NONCE_SALT; do
  fill_empty "$SALT" "$(openssl rand -base64 48)"
done

echo ""
echo "=== Generated credentials ==="
echo ""
echo "  WordPress:"
echo "    Admin password:  $WP_ADMIN_PASSWORD"
echo "    DB password:     $DB_PASSWORD"
echo "    DB root:         $WP_DB_ROOT_PASSWORD"
echo ""
echo "  OJS:"
echo "    Admin password:  $OJS_ADMIN_PASSWORD"
echo "    DB password:     $OJS_DB_PASSWORD"
echo "    DB root:         $OJS_DB_ROOT_PASSWORD"
echo ""
echo "  API key:           $WPOJS_API_KEY"
echo ""
echo "  Saved to: $ENV_FILE"
