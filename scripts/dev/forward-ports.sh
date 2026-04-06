#!/bin/bash
# Start socat port forwarders for DinD devcontainer.
# Maps localhost ports to Docker Compose service hostnames so the browser
# can reach WP/OJS/Adminer via VS Code port forwarding.
#
# Safe to run repeatedly — kills stale forwarders first.
# Called by: postStartCommand (devcontainer.json), rebuild-dev.sh, or manually.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Wait for Docker daemon to be ready (DinD can lag on container start) ---
MAX_DOCKER_WAIT=30
for i in $(seq 1 "$MAX_DOCKER_WAIT"); do
  if docker info >/dev/null 2>&1; then
    break
  fi
  if [ "$i" = "$MAX_DOCKER_WAIT" ]; then
    echo "[FAIL] Docker daemon not ready after ${MAX_DOCKER_WAIT}s — cannot forward ports"
    exit 1
  fi
  sleep 1
done

# Ensure compose services are running (DinD-aware).
# After a devcontainer rebuild, containers may exist but not be started.
source "$SCRIPT_DIR/../lib/dc.sh"
init_dc
$DC up -d 2>/dev/null || echo "[WARN] docker compose up failed — services may need manual start"

# Network name matches docker-compose.yml "sea-net" network with project prefix.
NETWORK="${COMPOSE_PROJECT_NAME:-pharkie-ojs-plugins}_sea-net"
CONTAINER_ID=$(cat /etc/hostname)

# Connect devcontainer to the compose network (idempotent)
docker network connect "$NETWORK" "$CONTAINER_ID" 2>/dev/null || true

# --- Wait for DNS resolution of service hostnames (network just connected) ---
MAX_DNS_WAIT=10
for i in $(seq 1 "$MAX_DNS_WAIT"); do
  if getent hosts ojs >/dev/null 2>&1; then
    break
  fi
  if [ "$i" = "$MAX_DNS_WAIT" ]; then
    echo "[WARN] DNS for 'ojs' not resolving after ${MAX_DNS_WAIT}s — forwarding may fail"
  fi
  sleep 1
done

# Kill any stale socat forwarders
pkill -f '[s]ocat.*TCP-LISTEN' 2>/dev/null || true
sleep 0.5

FORWARDS="8080:wp:80 8081:ojs:80 8082:adminer:8080"
ALL_OK=true

for FORWARD in $FORWARDS; do
  LOCAL_PORT="${FORWARD%%:*}"
  REMOTE="${FORWARD#*:}"
  REMOTE_HOST="${REMOTE%%:*}"
  REMOTE_PORT="${REMOTE#*:}"

  socat "TCP-LISTEN:$LOCAL_PORT,fork,reuseaddr" "TCP:$REMOTE_HOST:$REMOTE_PORT" 2>/dev/null &
  disown

  # Wait up to 3s for it to start listening
  for i in $(seq 1 6); do
    if bash -c "echo >/dev/tcp/localhost/$LOCAL_PORT" 2>/dev/null; then
      echo "[ok] localhost:$LOCAL_PORT → $REMOTE_HOST:$REMOTE_PORT"
      break
    fi
    if [ "$i" = "6" ]; then
      echo "[FAIL] localhost:$LOCAL_PORT → $REMOTE_HOST:$REMOTE_PORT"
      ALL_OK=false
    fi
    sleep 0.5
  done
done

if [ "$ALL_OK" = true ]; then
  echo "[ok] All ports forwarded."
else
  echo "[WARN] Some port forwards failed (services may not be running yet)."
fi
