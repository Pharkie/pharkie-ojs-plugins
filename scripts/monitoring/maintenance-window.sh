#!/bin/bash
# Pause and resume the Better Stack monitors around a deploy.
#
# Usage:
#   scripts/monitoring/maintenance-window.sh --pause     # before an import/deploy
#   scripts/monitoring/maintenance-window.sh --resume    # after, once verified
#   scripts/monitoring/maintenance-window.sh --status    # what's paused right now
#
# WHY THIS EXISTS
#   Every run-book says "Pause the Better Stack monitors" and none of them said
#   how, so it was a click-around-the-dashboard step that is easy to half-do and
#   easy to forget to undo. An issue reimport takes articles offline for a minute
#   or two and will page whoever is on call.
#
#   --resume is the half that matters. A monitor left paused is worse than one
#   that alerted: the site is unwatched and nothing says so. --status is there so
#   that is answerable in one command.
#
# SCOPE
#   URL monitors only. Heartbeat monitors are push-based — the cron job on the
#   box keeps pinging through a deploy, so pausing them would be pointless and
#   forgetting to resume them would silently disable the backup alarm.
#
# Requires:
#   BETTERSTACK_API_TOKEN — a *Team* API token (Better Stack -> Settings ->
#   API tokens). Auto-loaded from private/.env.live via sops if not exported.
set -o pipefail

API_BASE="https://uptime.betterstack.com/api/v2"
ACTION=""
for arg in "$@"; do
  case "$arg" in
    --pause) ACTION="pause" ;;
    --resume) ACTION="resume" ;;
    --status) ACTION="status" ;;
    # Portability, both parts learned the hard way on macOS: `head -n -1` is a
    # GNU-ism BSD head rejects, and `\?` is a GNU sed extension BSD sed matches
    # literally. This repo is driven from macOS and the Linux devcontainer both.
    --help|-h) sed -n '2,/^set -o/{/^set -o/d; s/^#[[:space:]]\{0,1\}//; p;}' "$0"; exit 0 ;;
    *) echo "ERROR: unknown argument '$arg'"; exit 1 ;;
  esac
done

if [ -z "$ACTION" ]; then
  echo "ERROR: one of --pause, --resume or --status is required"
  echo "Usage: scripts/monitoring/maintenance-window.sh --pause|--resume|--status"
  exit 1
fi

# Same loading path as setup-betterstack.sh, so there is one place to put the token.
#
# The trailing-comment strip is load-bearing. The line in .env.live is:
#   BETTERSTACK_API_TOKEN=<token> # Uptime API token, restored ... (also in keychain)
# and a plain `cut -d= -f2-` hands back token AND comment — 116 characters where
# the token is 24. Better Stack then answers "Invalid Team API token", which
# reads exactly like an expired credential and sent me looking for a new one on
# 2026-08-03 when the stored token was fine all along.
if [ -z "${BETTERSTACK_API_TOKEN+x}" ]; then
  REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
  ENV_LIVE="$REPO_ROOT/private/.env.live"
  if [ -f "$ENV_LIVE" ] && command -v sops >/dev/null 2>&1; then
    BETTERSTACK_API_TOKEN=$(sops -d "$ENV_LIVE" 2>/dev/null \
      | grep '^BETTERSTACK_API_TOKEN=' | head -1 | cut -d= -f2- \
      | sed 's/[[:space:]]*#.*$//; s/^[[:space:]]*//; s/[[:space:]]*$//' \
      | tr -d '"'"'")
    export BETTERSTACK_API_TOKEN
  fi
fi

if [ -z "$BETTERSTACK_API_TOKEN" ]; then
  echo "ERROR: BETTERSTACK_API_TOKEN not set"
  echo "  Store it in private/.env.live (sops), or: export BETTERSTACK_API_TOKEN=..."
  exit 1
fi

bs_api() {
  local method="$1" path="$2" data="$3" response status body
  if [ -n "$data" ]; then
    response=$(curl -s -w '\n%{http_code}' -X "$method" "$API_BASE$path" \
      -H "Authorization: Bearer $BETTERSTACK_API_TOKEN" \
      -H "Content-Type: application/json" -d "$data")
  else
    response=$(curl -s -w '\n%{http_code}' -X "$method" "$API_BASE$path" \
      -H "Authorization: Bearer $BETTERSTACK_API_TOKEN")
  fi
  status="${response##*$'\n'}"
  body="${response%$'\n'*}"
  printf '%s' "$body"
  [ "${status:-0}" -ge 200 ] && [ "${status:-0}" -lt 300 ]
}

MONITORS=$(bs_api GET "/monitors?per_page=50")
if [ $? -ne 0 ]; then
  # A stale token is the likeliest cause and the error text is unmissable, so
  # surface it rather than reporting "0 monitors" and carrying on.
  echo "ERROR: could not list monitors."
  echo "$MONITORS" | head -c 400
  echo
  echo "  If this says 'Invalid Team API token', mint a new one at"
  echo "  https://uptime.betterstack.com -> Settings -> API tokens, and update"
  echo "  BETTERSTACK_API_TOKEN in private/.env.live."
  exit 1
fi

# id<TAB>paused<TAB>name, one per line
PARSED=$(printf '%s' "$MONITORS" | python3 -c "
import json,sys
for m in json.load(sys.stdin).get('data', []):
    a = m.get('attributes', {})
    print(f\"{m['id']}\t{a.get('paused')}\t{a.get('pronounceable_name') or a.get('url','')}\")
")

if [ -z "$PARSED" ]; then
  echo "No URL monitors found. Nothing to do."
  exit 0
fi

if [ "$ACTION" = "status" ]; then
  echo "$PARSED" | while IFS=$'\t' read -r id paused name; do
    [ "$paused" = "True" ] && state="PAUSED " || state="active "
    echo "  $state $name"
  done
  n=$(echo "$PARSED" | grep -c $'\tTrue\t')
  echo ""
  if [ "$n" -gt 0 ]; then
    echo "  $n monitor(s) PAUSED — the site is unwatched. Run --resume when the deploy is verified."
  else
    echo "  All monitors active."
  fi
  exit 0
fi

[ "$ACTION" = "pause" ] && WANT="true" || WANT="false"
changed=0; skipped=0; failed=0
while IFS=$'\t' read -r id paused name; do
  [ -z "$id" ] && continue
  current=$([ "$paused" = "True" ] && echo true || echo false)
  if [ "$current" = "$WANT" ]; then skipped=$((skipped+1)); continue; fi
  if bs_api PATCH "/monitors/$id" "{\"paused\":$WANT}" >/dev/null; then
    changed=$((changed+1)); echo "  ${ACTION}d: $name"
  else
    failed=$((failed+1)); echo "  FAILED to $ACTION: $name"
  fi
done <<< "$PARSED"

echo ""
echo "  ${changed} changed, ${skipped} already ${WANT/true/paused}, ${failed} failed."
if [ "$ACTION" = "pause" ]; then
  echo "  Monitors are off. Run --resume as soon as the deploy is verified."
fi
[ "$failed" -eq 0 ]
