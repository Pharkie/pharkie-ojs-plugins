#!/bin/bash
# Query the old PKP-hosted OJS instance via session-authenticated API.
#
# Usage:
#   scripts/ojs/pkp-api.sh /contexts/1          # GET journal settings
#   scripts/ojs/pkp-api.sh /users               # GET all users
#   scripts/ojs/pkp-api.sh /issues              # GET all issues
#   scripts/ojs/pkp-api.sh /submissions         # GET all submissions
#
# Authenticates via session cookie (auto-refreshes on 403).
# Requires curl and python3.
set -eo pipefail

PKP_BASE="https://57.129.54.173/~journalexistenti/index.php/t1"
PKP_API="$PKP_BASE/api/v1"
COOKIE_FILE="/tmp/pkp-ojs-cookies.txt"
PKP_USER="ojsadmin"
PKP_PASS="M6lT5ktEA53G"

pkp_login() {
  # Get login page + CSRF token
  local login_html
  login_html=$(curl -s -k -c "$COOKIE_FILE" "$PKP_BASE/login")
  local csrf
  csrf=$(echo "$login_html" | grep -oP 'name="csrfToken" value="\K[^"]+')
  if [ -z "$csrf" ]; then
    echo "ERROR: Could not extract CSRF token" >&2
    return 1
  fi
  # Submit login
  local http_code
  http_code=$(curl -s -k -b "$COOKIE_FILE" -c "$COOKIE_FILE" -X POST \
    "$PKP_BASE/login/signIn" \
    -d "username=$PKP_USER&password=$PKP_PASS&csrfToken=$csrf" \
    -o /dev/null -w "%{http_code}")
  if [ "$http_code" != "302" ]; then
    echo "ERROR: Login failed (HTTP $http_code)" >&2
    return 1
  fi
}

pkp_get() {
  local endpoint="$1"
  shift
  local url="$PKP_API$endpoint"

  # Try with existing cookie
  if [ -f "$COOKIE_FILE" ]; then
    local resp
    resp=$(curl -s -k -b "$COOKIE_FILE" "$url" "$@" 2>&1)
    if ! echo "$resp" | grep -q '"api.403.unauthorized"'; then
      echo "$resp"
      return 0
    fi
  fi

  # Cookie expired or missing — re-login
  pkp_login || return 1
  curl -s -k -b "$COOKIE_FILE" "$url" "$@"
}

if [ -z "$1" ]; then
  echo "Usage: $0 /endpoint [curl args...]"
  echo "Examples:"
  echo "  $0 /contexts/1"
  echo "  $0 /users"
  echo "  $0 /issues"
  echo "  $0 /submissions?count=5"
  exit 1
fi

pkp_get "$@" | python3 -m json.tool 2>/dev/null || pkp_get "$@"
