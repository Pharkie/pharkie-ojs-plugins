#!/bin/bash
# Shared helper functions for monitor-safe.sh and monitor-deep.sh.
# Source this file after resolve-ssh.sh has set SSH_CMD.

REMOTE_DIR="/opt/pharkie-ojs-plugins"
PASSED=0
FAILED=0
TOTAL=0
FAILURE_DETAILS=""
_HEARTBEAT_URLS=()  # populated by register_heartbeat

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

# Register a heartbeat URL so the exit trap can ping it on crash.
register_heartbeat() {
  [ -n "$1" ] && _HEARTBEAT_URLS+=("$1")
}

# Exit trap: ensures heartbeats always get pinged, even on crash/exit 2.
# Without this, a script crash = missed heartbeat = false alert.
_ping_heartbeats_on_exit() {
  local exit_code=$?
  if [ "$exit_code" -ne 0 ] && [ ${#_HEARTBEAT_URLS[@]} -gt 0 ]; then
    echo ""
    echo "  [TRAP] Script exited with code $exit_code — pinging heartbeats as failed"
    for url in "${_HEARTBEAT_URLS[@]}"; do
      curl -sf -d "Script crashed with exit code $exit_code" "$url/fail" > /dev/null 2>&1 || true
    done
  fi
}
trap _ping_heartbeats_on_exit EXIT

# Retry wrapper for SSH commands. Retries up to 3 times with backoff.
# Handles transient network/SSH failures from GitHub Actions runners.
ssh_retry() {
  local max_attempts=3
  local attempt=1
  local delay=5
  local result
  while [ $attempt -le $max_attempts ]; do
    if result=$("$@" 2>&1); then
      echo "$result"
      return 0
    fi
    if [ $attempt -lt $max_attempts ]; then
      echo "  [RETRY] Attempt $attempt/$max_attempts failed, retrying in ${delay}s..." >&2
      sleep $delay
      delay=$((delay * 2))
    fi
    attempt=$((attempt + 1))
  done
  echo "$result"
  return 1
}

remote() {
  ssh_retry $SSH_CMD "cd $REMOTE_DIR && $*"
}

wp_cli() {
  remote "$COMPOSE exec -T wp wp --allow-root $*"
}

# Read a required value from remote .env. Returns 1 on failure instead
# of exiting — caller should check return code or use || to handle.
require_env() {
  local var_name="$1"
  local value
  value=$(ssh_retry $SSH_CMD "grep '^${var_name}=' $REMOTE_DIR/.env | cut -d= -f2") || value=""
  if [ -z "$value" ]; then
    fail "Could not read $var_name from remote .env (SSH may be broken)"
    return 1
  fi
  echo "$value"
}

# Read an optional value from remote .env. Returns empty string on failure.
read_env() {
  local var_name="$1"
  ssh_retry $SSH_CMD "grep '^${var_name}=' $REMOTE_DIR/.env | cut -d= -f2" 2>/dev/null || echo ""
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
  result=$(ssh_retry $SSH_CMD "$@") || result=""
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
  compose_files=$(ssh_retry $SSH_CMD "docker inspect --format='{{index .Config.Labels \"com.docker.compose.project.config_files\"}}' \$(docker ps -q --filter 'label=com.docker.compose.project=pharkie-ojs-plugins' | head -1) 2>/dev/null") || compose_files=""
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
