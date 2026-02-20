#!/bin/bash
# OJS config templating — generates config.inc.php from environment variables.
# Runs before the PKP startup script (which generates SSL certs + starts Apache).
set -e

CONFIG=/var/www/html/config.inc.php
TEMPLATE=/etc/ojs/config.inc.php.tmpl

# Generate config from template if not already installed
if [ ! -f "$CONFIG" ] || grep -q "installed = Off" "$CONFIG"; then
  echo "[SEA] Generating config.inc.php from template..."
  envsubst < "$TEMPLATE" > "$CONFIG"
  chown www-data:www-data "$CONFIG" 2>/dev/null || true
  chmod 640 "$CONFIG"
fi

# Hand off to PKP's own startup (generates SSL certs, starts Apache)
exec /usr/local/bin/pkp-start
