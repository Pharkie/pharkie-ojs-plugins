#!/bin/bash
# Set up Better Stack uptime monitors and heartbeats via API.
# Idempotent — checks for existing monitors and heartbeats by name before creating.
#
# What it manages: the ten URL monitors and the four OJS heartbeats defined
# below, each as Better Stack has it (checked against the API on 2026-10-18).
# The account's other two heartbeats, "SEA: Harbour nightly backup" and
# "SEA: Harbour browser checks", are Harbour's (docs/on-call.md in
# Pharkie/sea-membership-platform). This script never creates, changes or
# deletes them, --delete-all included.
#
# Usage:
#   scripts/monitoring/setup-betterstack.sh --host=sea-live                       # Create what is missing
#   scripts/monitoring/setup-betterstack.sh --host=sea-live --dry-run             # Show what would be created
#   scripts/monitoring/setup-betterstack.sh --host=sea-live --delete-all          # Delete the monitors and heartbeats defined here
#   scripts/monitoring/setup-betterstack.sh --host=sea-live --update-heartbeats   # PATCH the four heartbeats to the config below
#
# 🛑 Never run --delete-all against the live account. A recreated heartbeat gets
# a new URL, and every job still pinging the old one leaves it Down until each
# copy is replaced (private/docs/monitoring.md, "Where each heartbeat URL is read").
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
    # Strip any trailing `# comment` and surrounding quotes. The line in
    # .env.live carries one, so a bare `cut -d= -f2-` returns 116 characters
    # for a 24-character token and Better Stack rejects it as an invalid token
    # — which reads like an expired credential rather than a parsing bug.
    BETTERSTACK_API_TOKEN=$(sops -d "$ENV_LIVE" 2>/dev/null \
      | grep '^BETTERSTACK_API_TOKEN=' | head -1 | cut -d= -f2- \
      | sed 's/[[:space:]]*#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//' \
      | tr -d '"'"'")
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
OJS_BASE_URL=$($SSH_CMD "grep '^OJS_BASE_URL=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_PATH=$($SSH_CMD "grep '^OJS_JOURNAL_PATH=' $REMOTE_DIR/.env | cut -d= -f2")
OJS_JOURNAL_URL="${OJS_BASE_URL}/index.php/${OJS_JOURNAL_PATH}"

# The two WP monitors watch the live WordPress, which is on Krystal, not on the
# box. The box's WordPress (its WP_HOME and CADDY_WP_DOMAIN) is the test rig,
# wp-staging, so its URL is never a monitor's: read from the box's .env, it
# would recreate both monitors on the rig.
WP_PUBLIC_URL="https://community.existentialanalysis.org.uk"

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

# --- Delete what this script defines ---
# --delete-all deletes by walking the definitions below: in that mode
# create_monitor and create_heartbeat delete their own name instead of creating
# it. Anything else whose name starts "SEA:" (Harbour's two heartbeats, or a
# monitor added in the UI) is not defined here, so it is left alone.
DELETED=0

delete_defined() {
  local kind="$1" name="$2" id="$3"   # kind: monitors | heartbeats
  if [ -z "$id" ]; then
    echo "  [SKIP] $name (not in Better Stack)"
    return
  fi
  if [ "$DRY_RUN" = true ]; then
    echo "  [DRY-RUN] Would delete ${kind%s}: $name (ID: $id)"
    DELETED=$((DELETED + 1))
  elif bs_api DELETE "/$kind/$id" > /dev/null; then
    echo "  Deleted ${kind%s}: $name"
    DELETED=$((DELETED + 1))
  else
    echo "  [ERROR] Failed to delete ${kind%s}: $name"
  fi
}

# --- Define monitors ---
# Prefix all names with "SEA:" for easy identification
CREATED=0
SKIPPED=0

create_monitor() {
  local name="$1"
  local json="$2"

  if [ "$DELETE_ALL" = true ]; then
    delete_defined monitors "$name" "$(monitor_id "$name")"
    return
  fi

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

if [ "$DELETE_ALL" = true ]; then
  echo "Deleting the monitors defined here..."
else
  echo "Creating monitors..."
fi
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

# 2. WP REST API — push-only (email muted; WP Homepage is the alarming primary)
create_monitor "SEA: WP REST API" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "$WP_PUBLIC_URL/wp-json/",
  "pronounceable_name": "SEA: WP REST API",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "email": false,
  "push": true,
  "regions": ["eu", "us"]
}
EOF
)"

# 3. WP Admin — REMOVED 2026-08-03. It watched wp-staging, which is a test
# rig that will never be promoted (see the WP_PUBLIC_URL note above), so an
# alert on it is noise about something nobody would act on.
# It had no live counterpart either: defined here but absent from Better
# Stack, one of three such drifts found when the definitions were
# reconciled against the API.

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



# 9. Stripe webhook route exists (GET returns 405 Method Not Allowed, not 404)
# Dropped: Better Stack treats 400/405 as failure. Stripe config is verified
# by the hourly SSH checks instead (Stripe API key valid, plugin active).

# SEA: OJS Login Page and SEA: OJS Index Redirect were defined here for months
# and never existed in Better Stack. Not deleted by anyone — the account caps at
# 10 monitors and the API refused them: "Monitor quota reached." Removed
# 2026-08-03 so this file describes what actually runs, and so --dry-run stops
# reporting work it can never do.
#
# The journal is not unwatched: OJS Homepage, OJS Journal and OJS Article
# Content cover it, and the last of those is a keyword check that catches the
# site serving pages with no articles. If the quota is ever raised — Harbour
# cut-over will want monitors for the member area, checkout and email sending —
# these two are worth reinstating.

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

# 11-14. Added in the Better Stack UI, not here, and so invisible to this script
# until 2026-08-03. Config captured from the live monitors so re-creating them
# reproduces what is actually running. (--delete-all now deletes only what is
# defined here, so a monitor added in the UI and not written down here would
# survive it rather than be lost.)

create_monitor "SEA: Docs Knowledge Base" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "https://docs.existentialanalysis.org.uk",
  "pronounceable_name": "SEA: Docs Knowledge Base",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "recovery_period": 180,
  "verify_ssl": true,
  "follow_redirects": true,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

create_monitor "SEA: New Site Homepage" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "https://newsite.existentialanalysis.org.uk",
  "pronounceable_name": "SEA: New Site Homepage",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "recovery_period": 180,
  "verify_ssl": true,
  "follow_redirects": true,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

create_monitor "SEA: Harbour Health" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "https://harbour.existentialanalysis.org.uk/api/health",
  "pronounceable_name": "SEA: Harbour Health",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "recovery_period": 180,
  "verify_ssl": true,
  "follow_redirects": true,
  "email": true,
  "regions": ["eu", "us"]
}
EOF
)"

create_monitor "SEA: Umami Analytics" "$(cat <<EOF
{
  "monitor_type": "status",
  "url": "https://analytics.existentialanalysis.org.uk/api/heartbeat",
  "pronounceable_name": "SEA: Umami Analytics",
  "check_frequency": $FREQ,
  "confirmation_period": $CONFIRM,
  "request_timeout": 15,
  "recovery_period": 180,
  "verify_ssl": true,
  "follow_redirects": true,
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
#
# These four are the OJS heartbeats, each exactly as Better Stack has it, so
# --update-heartbeats changes nothing. Harbour's two are not defined here.
#
# Notification fields: email, SMS and push are on and phone calls off, as in
# Better Stack's settings. The plan does not deliver SMS or push (incident
# timelines read "Failed to text … because your subscription is no longer
# active"), so only the emails go out. The fields stay as set, so a renewed
# plan needs no change here.

HB_CREATED=0
HB_SKIPPED=0

HB_UPDATED=0

create_heartbeat() {
  local name="$1"
  local json="$2"

  if [ "$DELETE_ALL" = true ]; then
    delete_defined heartbeats "$name" "$(heartbeat_id "$name")"
    return
  fi

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
if [ "$DELETE_ALL" = true ]; then
  echo "Deleting the heartbeats defined here..."
else
  echo "Creating heartbeats..."
fi
echo ""

# 1. Hourly monitoring: 1 h period, 30 min grace.
# Pinged by the box's own cron at :15 (see private/docs/monitoring.md), and by
# phase 1 of the daily run. The grace lets a run be up to 30 minutes late; a
# skipped run alerts.
create_heartbeat "SEA: Hourly monitoring" "$(cat <<EOF
{
  "name": "SEA: Hourly monitoring",
  "period": 3600,
  "grace": 1800,
  "call": false,
  "sms": true,
  "email": true,
  "push": true,
  "critical_alert": false
}
EOF
)"

# 2. Daily monitoring: 1 day period, 2 h grace.
# Pinged by monitor-daily.yml in the private repo (06:30 UTC), and also by
# Harbour's daily run (06:45 UTC), so a Harbour pass can resolve an OJS failure.
# Defined here all the same: Harbour does not create or manage it.
create_heartbeat "SEA: Daily monitoring" "$(cat <<EOF
{
  "name": "SEA: Daily monitoring",
  "period": 86400,
  "grace": 7200,
  "call": false,
  "sms": true,
  "email": true,
  "push": true,
  "critical_alert": false
}
EOF
)"

# 3. Database backup: 1 day period, 2 h grace.
# Pinged by root's 03:00 crontab line on the exit status of backup-ojs-db.sh.
create_heartbeat "SEA: Database backup" "$(cat <<EOF
{
  "name": "SEA: Database backup",
  "period": 86400,
  "grace": 7200,
  "call": false,
  "sms": true,
  "email": true,
  "push": true,
  "critical_alert": false
}
EOF
)"

# 4. OJS scheduled tasks: 1 h period, 30 min grace.
# Pinged by ojs-scheduler-heartbeat.sh from the cron inside the ojs container
# (both written by docker/ojs/entrypoint.sh).
create_heartbeat "SEA: OJS scheduled tasks" "$(cat <<EOF
{
  "name": "SEA: OJS scheduled tasks",
  "period": 3600,
  "grace": 1800,
  "call": false,
  "sms": true,
  "email": true,
  "push": true,
  "critical_alert": false
}
EOF
)"

# There is no "SEA: GitHub backup pull". Its workflow, backup.yml in the private
# repo, was deleted with the heartbeat on 2026-08-29, when R2 replaced it.

echo ""
if [ "$DELETE_ALL" = true ]; then
  echo "=== Done: $DELETED deleted ==="
  if [ "$DRY_RUN" = true ]; then
    echo "(Dry run — nothing was actually deleted)"
  fi
  exit 0
fi
echo "=== Done: $CREATED monitors created, $SKIPPED skipped; $HB_CREATED heartbeats created, $HB_UPDATED updated, $HB_SKIPPED skipped ==="
if [ "$DRY_RUN" = true ]; then
  echo "(Dry run — no monitors were actually created)"
fi
echo ""
echo "Dashboard: https://uptime.betterstack.com/team/0/monitors"
echo "Status page: configure at https://uptime.betterstack.com/team/0/status-pages"
