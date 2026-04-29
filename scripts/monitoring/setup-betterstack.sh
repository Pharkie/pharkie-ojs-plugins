#!/bin/bash
# Set up Better Stack uptime monitors via API.
# Idempotent — checks for existing monitors by name before creating.
#
# Usage:
#   scripts/monitoring/setup-betterstack.sh --host=sea-live                       # Create monitors
#   scripts/monitoring/setup-betterstack.sh --host=sea-live --dry-run             # Show what would be created
#   scripts/monitoring/setup-betterstack.sh --host=sea-live --delete-all          # Remove all SEA monitors
#   scripts/monitoring/setup-betterstack.sh --host=sea-live --update-heartbeats   # PATCH existing heartbeats to match script-defined config
#
# Public status page: https://status.existentialanalysis.org.uk/
# Admin dashboard:    https://uptime.betterstack.com
#
# Requires:
#   BETTERSTACK_API_TOKEN env var (from Better Stack → Settings → API tokens).
#   Auto-loaded from private/.env.live via sops if not already exported.
set -o pipefail

# --- Parse arguments ---
SSH_HOST=""
DRY_RUN=false
DELETE_ALL=false
UPDATE_HEARTBEATS=false
for arg in "$@"; do
  case "$arg" in
    --host=*) SSH_HOST="${arg#--host=}" ;;
    --dry-run) DRY_RUN=true ;;
    --delete-all) DELETE_ALL=true ;;
    --update-heartbeats) UPDATE_HEARTBEATS=true ;;
  esac
done

if [ -z "$SSH_HOST" ]; then
  echo "ERROR: --host=<server> is required"
  echo "Usage: scripts/monitoring/setup-betterstack.sh --host=sea-live [--dry-run]"
  exit 1
fi

# Auto-load BETTERSTACK_API_TOKEN from private/.env.live if unset (devcontainer/local convenience).
# Skipped when explicitly set (incl. set-but-empty, e.g. tests) so behavior stays predictable in CI.
if [ -z "${BETTERSTACK_API_TOKEN+x}" ]; then
  ENV_LIVE="$(cd "$(dirname "$0")/../.." && pwd)/private/.env.live"
  if [ -f "$ENV_LIVE" ] && command -v sops >/dev/null 2>&1; then
    BETTERSTACK_API_TOKEN=$(sops -d "$ENV_LIVE" 2>/dev/null | grep '^BETTERSTACK_API_TOKEN=' | head -1 | cut -d= -f2-)
    export BETTERSTACK_API_TOKEN
  fi
fi

if [ -z "$BETTERSTACK_API_TOKEN" ]; then
  echo "ERROR: BETTERSTACK_API_TOKEN not set"
  echo "  Devcontainer/local: should auto-load from private/.env.live (needs sops + matching age key)"
  echo "  Manual:             export BETTERSTACK_API_TOKEN=...   (Better Stack → Settings → API tokens)"
  exit 1
fi

API_BASE="https://uptime.betterstack.com/api/v2"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# --- Resolve server and read URLs ---
source "$SCRIPTS_ROOT/lib/resolve-ssh.sh"
resolve_ssh "$SSH_HOST"

REMOTE_DIR="/opt/pharkie-ojs-plugins"
WP_HOME=$($SSH_CMD "grep '^WP_HOME=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_BASE_URL=$($SSH_CMD "grep '^OJS_BASE_URL=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_PATH=$($SSH_CMD "grep '^OJS_JOURNAL_PATH=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"

# Better Stack monitors must use publicly reachable URLs.
# WP_HOME may be an internal Docker address — derive public URL from Caddy config.
CADDY_WP=$($SSH_CMD "grep '^CADDY_WP_DOMAIN=' $REMOTE_DIR/.env | cut -d= -f2" 2>/dev/null)
if [ -n "$CADDY_WP" ]; then
  WP_PUBLIC_URL="https://$CADDY_WP"
elif echo "$WP_HOME" | grep -qE '^https?://(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.|localhost|127\.|.*:[0-9]{4,})'; then
  echo "ERROR: WP_HOME ($WP_HOME) is internal and CADDY_WP_DOMAIN is not set."
  echo "       Better Stack needs a public URL. Set CADDY_WP_DOMAIN in .env on the server."
  exit 1
else
  WP_PUBLIC_URL="$WP_HOME"
fi

echo "=== Better Stack Monitor Setup ==="
echo "    WP:  $WP_PUBLIC_URL"
echo "    OJS: $OJS_BASE_URL"
echo "    Server IP: $SERVER_IP"
echo ""

# --- API helpers ---
bs_api() {
  local method="$1"
  local path="$2"
  local data="$3"
  local response status body
  if [ -n "$data" ]; then
    response=$(curl -s -w '\n%{http_code}' -X "$method" "$API_BASE$path" \
      -H "Authorization: Bearer $BETTERSTACK_API_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$data")
  else
    response=$(curl -s -w '\n%{http_code}' -X "$method" "$API_BASE$path" \
      -H "Authorization: Bearer $BETTERSTACK_API_TOKEN")
  fi
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"
  printf '%s' "$body"
  [ "${status:-0}" -ge 200 ] && [ "${status:-0}" -lt 300 ]
}

# --- Fetch existing monitors (paginated) ---
get_existing_monitors() {
  local page=1
  local all_monitors="[]"
  while true; do
    local response
    response=$(bs_api GET "/monitors?page=$page&per_page=50") || break
    local items
    items=$(echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps([m['attributes']['pronounceable_name'] + '|' + str(m['id']) for m in d.get('data',[])]))" 2>/dev/null) || break
    if [ "$items" = "[]" ]; then
      break
    fi
    all_monitors=$(echo "$all_monitors $items" | python3 -c "import sys,json; a=json.loads(sys.stdin.read().split()[0]); b=json.loads(sys.stdin.read().split()[0] if len(sys.stdin.read().split())>0 else '[]'); print(json.dumps(a+b))" 2>/dev/null || echo "$all_monitors")
    page=$((page + 1))
    # Safety: don't loop forever
    [ "$page" -gt 10 ] && break
  done
  echo "$all_monitors"
}

# Simpler approach: just get all monitor names
EXISTING=$(bs_api GET "/monitors?per_page=50" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('data', []):
    print(m['attributes']['pronounceable_name'] + '|' + m['id'])
" 2>/dev/null) || EXISTING=""

monitor_exists() {
  echo "$EXISTING" | grep -q "^$1|"
}

monitor_id() {
  echo "$EXISTING" | grep "^$1|" | cut -d'|' -f2
}

# --- Fetch existing heartbeats ---
EXISTING_HB=$(bs_api GET "/heartbeats?per_page=50" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for h in data.get('data', []):
    print(h['attributes']['name'] + '|' + h['id'] + '|' + h['attributes']['url'])
" 2>/dev/null) || EXISTING_HB=""

heartbeat_exists() {
  echo "$EXISTING_HB" | grep -q "^$1|"
}

heartbeat_id() {
  echo "$EXISTING_HB" | grep "^$1|" | cut -d'|' -f2
}

# --- Delete all SEA monitors and heartbeats ---
if [ "$DELETE_ALL" = true ]; then
  echo "Deleting all SEA monitors..."
  echo "$EXISTING" | grep "^SEA:" | while IFS='|' read -r name id; do
    if [ "$DRY_RUN" = true ]; then
      echo "  [DRY-RUN] Would delete monitor: $name (ID: $id)"
    else
      bs_api DELETE "/monitors/$id" > /dev/null
      echo "  Deleted monitor: $name"
    fi
  done
  echo "Deleting all SEA heartbeats..."
  echo "$EXISTING_HB" | grep "^SEA:" | while IFS='|' read -r name id url; do
    if [ "$DRY_RUN" = true ]; then
      echo "  [DRY-RUN] Would delete heartbeat: $name (ID: $id)"
    else
      bs_api DELETE "/heartbeats/$id" > /dev/null
      echo "  Deleted heartbeat: $name"
    fi
  done
  exit 0
fi

# --- Define monitors ---
# Prefix all names with "SEA:" for easy identification
CREATED=0
SKIPPED=0

create_monitor() {
  local name="$1"
  local json="$2"

  if monitor_exists "$name"; then
    echo "  [SKIP] $name (already exists)"
    SKIPPED=$((SKIPPED + 1))
    return
  fi

  if [ "$DRY_RUN" = true ]; then
    echo "  [DRY-RUN] Would create: $name"
    echo "            $json" | python3 -m json.tool 2>/dev/null || echo "            $json"
    CREATED=$((CREATED + 1))
    return
  fi

  RESPONSE=$(bs_api POST "/monitors" "$json")
  if [ $? -eq 0 ]; then
    MONITOR_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['id'])" 2>/dev/null)
    echo "  [CREATED] $name (ID: $MONITOR_ID)"
    CREATED=$((CREATED + 1))
  else
    echo "  [ERROR] Failed to create $name"
    echo "          $RESPONSE"
  fi
}

# Check frequency: 180 = 3 min (Better Stack free tier minimum)
FREQ=180
# Confirmation period: require 5 min of consecutive failures before alerting.
# Filters out transient self-healing blips (e.g. brief 503s during WP auto-updates).
CONFIRM=300

echo "Creating monitors..."
echo ""

# 1. WP homepage
create_monitor "SEA: WP Homepage" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "$WP_PUBLIC_URL",
  "pronounceable_name": "SEA: WP Homepage",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 2. WP REST API
create_monitor "SEA: WP REST API" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "$WP_PUBLIC_URL/wp-json/",
  "pronounceable_name": "SEA: WP REST API",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 3. WP Admin
create_monitor "SEA: WP Admin" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "$WP_PUBLIC_URL/wp-admin/",
  "pronounceable_name": "SEA: WP Admin",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "follow_redirects": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 4. OJS homepage
create_monitor "SEA: OJS Homepage" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "$OJS_BASE_URL",
  "pronounceable_name": "SEA: OJS Homepage",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 5. OJS journal page
create_monitor "SEA: OJS Journal" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "$OJS_JOURNAL_URL",
  "pronounceable_name": "SEA: OJS Journal",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 6. Keyword: Article page — find a known published article title
# Use a generic keyword that should always appear on the journal page
create_monitor "SEA: OJS Article Content" "$(cat <<EOF
{
  "monitor_type": "keyword",
  "url": "$OJS_JOURNAL_URL",
  "pronounceable_name": "SEA: OJS Article Content",
  "required_keyword": "Existential Analysis",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 7. Keyword: OJS login page
create_monitor "SEA: OJS Login Page" "$(cat <<EOF
{
  "monitor_type": "keyword",
  "url": "$OJS_JOURNAL_URL/login",
  "pronounceable_name": "SEA: OJS Login Page",
  "required_keyword": "Login",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 8. OJS /ea/index redirect (verifies Caddy redirect rule works)
create_monitor "SEA: OJS Index Redirect" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "$OJS_BASE_URL/ea/index",
  "pronounceable_name": "SEA: OJS Index Redirect",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": true,
  "follow_redirects": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 9. Stripe webhook route exists (GET returns 405 Method Not Allowed, not 404)
# Dropped: Better Stack treats 400/405 as failure. Stripe config is verified
# by the hourly SSH checks instead (Stripe API key valid, plugin active).

# 10. TCP: HTTPS port
create_monitor "SEA: HTTPS Port" "$(cat <<EOF
{
  "monitor_type": "tcp",
  "url": "$SERVER_IP",
  "port": "443",
  "pronounceable_name": "SEA: HTTPS Port",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 5000,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

# ============================================================
# HEARTBEATS
# ============================================================
# Heartbeats monitor that periodic jobs actually run.
# Each job pings its heartbeat URL on success, or /fail on error.
# If no ping arrives within period + grace, Better Stack creates an incident.

HB_CREATED=0
HB_SKIPPED=0

HB_UPDATED=0

create_heartbeat() {
  local name="$1"
  local json="$2"

  if heartbeat_exists "$name"; then
    if [ "$UPDATE_HEARTBEATS" = true ]; then
      local id
      id=$(heartbeat_id "$name")
      if [ "$DRY_RUN" = true ]; then
        echo "  [DRY-RUN] Would PATCH heartbeat: $name (ID: $id)"
        echo "            $json" | python3 -m json.tool 2>/dev/null || echo "            $json"
        HB_UPDATED=$((HB_UPDATED + 1))
        return
      fi
      RESPONSE=$(bs_api PATCH "/heartbeats/$id" "$json")
      if [ $? -eq 0 ]; then
        echo "  [UPDATED] $name"
        HB_UPDATED=$((HB_UPDATED + 1))
      else
        echo "  [ERROR] Failed to update heartbeat: $name"
        echo "          $RESPONSE"
      fi
      return
    fi
    echo "  [SKIP] $name (already exists)"
    HB_SKIPPED=$((HB_SKIPPED + 1))
    return
  fi

  if [ "$DRY_RUN" = true ]; then
    echo "  [DRY-RUN] Would create heartbeat: $name"
    echo "            $json" | python3 -m json.tool 2>/dev/null || echo "            $json"
    HB_CREATED=$((HB_CREATED + 1))
    return
  fi

  RESPONSE=$(bs_api POST "/heartbeats" "$json")
  if [ $? -eq 0 ]; then
    HB_URL=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['attributes']['url'])" 2>/dev/null)
    echo "  [CREATED] $name"
    echo "            URL: $HB_URL"
    HB_CREATED=$((HB_CREATED + 1))
  else
    echo "  [ERROR] Failed to create heartbeat: $name"
    echo "          $RESPONSE"
  fi
}

echo ""
echo "Creating heartbeats..."
echo ""

# 1. Hourly monitoring workflow (expect every 75 min, grace 75 min)
# Total tolerance 150 min — covers GitHub Actions cron drift (xx:00–xx:55) and
# a single auto-rerun cycle on transient runner-allocation failures (see
# private/.github/workflows/monitor-rerun.yml).
create_heartbeat "SEA: Hourly monitoring" "$(cat <<EOF
{
  "name": "SEA: Hourly monitoring",
  "period": 4500,
  "grace": 4500,
  "call": false,
  "sms": true,
  "email": true,
  "push": true
}
EOF
)"

# 2. Daily deep monitoring workflow (expect every 25 hours, grace 2 hours)
create_heartbeat "SEA: Daily monitoring" "$(cat <<EOF
{
  "name": "SEA: Daily monitoring",
  "period": 90000,
  "grace": 7200,
  "call": false,
  "sms": true,
  "email": true,
  "push": true
}
EOF
)"

# 3. Database backup (expect every 25 hours, grace 2 hours)
create_heartbeat "SEA: Database backup" "$(cat <<EOF
{
  "name": "SEA: Database backup",
  "period": 90000,
  "grace": 7200,
  "call": false,
  "sms": true,
  "email": true,
  "push": true
}
EOF
)"

# 4. OJS scheduled tasks cron (expect every 75 min, grace 30 min)
create_heartbeat "SEA: OJS scheduled tasks" "$(cat <<EOF
{
  "name": "SEA: OJS scheduled tasks",
  "period": 4500,
  "grace": 1800,
  "call": false,
  "sms": true,
  "email": true,
  "push": true
}
EOF
)"

# 5. GitHub backup pull workflow (expect every 25 hours, grace 2 hours)
create_heartbeat "SEA: GitHub backup pull" "$(cat <<EOF
{
  "name": "SEA: GitHub backup pull",
  "period": 90000,
  "grace": 7200,
  "call": false,
  "sms": true,
  "email": true,
  "push": true
}
EOF
)"

echo ""
echo "=== Done: $CREATED monitors created, $SKIPPED skipped; $HB_CREATED heartbeats created, $HB_UPDATED updated, $HB_SKIPPED skipped ==="
if [ "$DRY_RUN" = true ]; then
  echo "(Dry run — no monitors were actually created)"
fi
echo ""
echo "Dashboard: https://uptime.betterstack.com/team/0/monitors"
echo "Status page: configure at https://uptime.betterstack.com/team/0/status-pages"
