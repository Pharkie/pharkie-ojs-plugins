#!/bin/bash
# Shared helper functions for monitor-safe.sh and monitor-deep.sh.
# Source this file after resolve-ssh.sh has set SSH_CMD.

REMOTE_DIR="/opt/pharkie-ojs-plugins"
PASSED=0
FAILED=0
TOTAL=0
FAILURE_DETAILS=""

pass() {
  PASSED=$((PASSED + 1))
  TOTAL=$((TOTAL + 1))
  echo "  [PASS] $1"
}

fail() {
  FAILED=$((FAILED + 1))
  TOTAL=$((TOTAL + 1))
  echo "  [FAIL] $1"
  [ -n "$2" ] && echo "         $2"
  FAILURE_DETAILS="${FAILURE_DETAILS}${1}\n"
}

info() {
  echo "  [INFO] $1"
}

remote() {
  $SSH_CMD "cd $REMOTE_DIR && $*" 2>&1
}

wp_cli() {
  remote "$COMPOSE exec -T wp wp --allow-root $*"
}

# Read a required value from remote .env. Exits the script if empty
# (if SSH is broken, no checks will work — fail fast).
require_env() {
  local var_name="$1"
  local value
  value=$($SSH_CMD "grep '^${var_name}=' $REMOTE_DIR/.env | cut -d= -f2") || value=""
  if [ -z "$value" ]; then
    echo "FATAL: Could not read $var_name from remote .env (SSH may be broken)"
    exit 2
  fi
  echo "$value"
}

# Read an optional value from remote .env. Returns empty string on failure.
read_env() {
  local var_name="$1"
  $SSH_CMD "grep '^${var_name}=' $REMOTE_DIR/.env | cut -d= -f2" 2>/dev/null || echo ""
}

# Run a remote command and fail the check if output is empty.
# Usage: VALUE=$(require_remote "Check name" "command to run") || continue
require_remote() {
  local label="$1"; shift
  local result
  result=$(remote "$@") || result=""
  result=$(echo "$result" | tr -d '[:space:]')
  if [ -z "$result" ]; then
    fail "$label (remote command returned empty — SSH or container may be down)"
    return 1
  fi
  echo "$result"
}

# Run a direct SSH command and fail the check if output is empty.
# Usage: VALUE=$(require_ssh "Check name" "command") || continue
require_ssh() {
  local label="$1"; shift
  local result
  result=$($SSH_CMD "$@") || result=""
  result=$(echo "$result" | tr -d '[:space:]')
  if [ -z "$result" ]; then
    fail "$label (SSH command returned empty — connection may be broken)"
    return 1
  fi
  echo "$result"
}

# Auto-detect docker compose command from running containers.
detect_compose() {
  local compose_files
  compose_files=$($SSH_CMD "docker inspect --format='{{index .Config.Labels \"com.docker.compose.project.config_files\"}}' \$(docker ps -q --filter 'label=com.docker.compose.project=pharkie-ojs-plugins' | head -1) 2>/dev/null") || compose_files=""
  if [ -n "$compose_files" ]; then
    COMPOSE="docker compose"
    IFS=',' read -ra FILES <<< "$compose_files"
    for f in "${FILES[@]}"; do
      COMPOSE="$COMPOSE -f $(basename "$f")"
    done
  else
    # Auto-detect failed — use bare docker compose (uses docker-compose.yml only).
    # This may miss services in overlay files (caddy, staging) but is safer than
    # guessing which overlay is active.
    COMPOSE="docker compose"
  fi
}

# Ping a Better Stack heartbeat with failure details.
ping_heartbeat() {
  local hb_url="$1"
  local failed_count="$2"
  local passed_count="$3"
  local total_count="$4"
  [ -z "$hb_url" ] && return

  if [ "$failed_count" -gt 0 ]; then
    curl -sf -d "$(printf "$FAILURE_DETAILS")" "$hb_url/fail" > /dev/null 2>&1 || true
  else
    curl -sf "$hb_url" > /dev/null 2>&1 || true
  fi
}
