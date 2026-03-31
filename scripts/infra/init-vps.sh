#!/bin/bash
# One-time VPS setup: create server, firewall, SSH config.
# Runs FROM the devcontainer. Requires hcloud CLI with an active context.
#
# Usage:
#   scripts/infra/init-vps.sh --name=sea-staging                     # Create new server
#   scripts/infra/init-vps.sh --name=sea-staging --ssl                # Also open ports 80/443
#   scripts/infra/init-vps.sh --name=sea-staging --type=cpx22         # Custom server type
#   scripts/infra/init-vps.sh --name=sea-staging --location=fsn1      # Custom location
#   scripts/infra/init-vps.sh --name=sea-staging --skip-server        # Skip server creation (already exists)
#
# After this script completes, run:
#   1. Create .env from .env.example and edit it
#   2. scripts/infra/deploy.sh --host=<name> --provision --env-file=.env.staging
set -eo pipefail

# --- Defaults ---
SERVER_NAME=""
SERVER_TYPE="cpx22"
LOCATION="nbg1"
SSH_KEY_PATH="$HOME/.ssh/hetzner"
SSH_KEY_NAME="hetzner"
OPEN_SSL_PORTS=""
SKIP_SERVER=""
# --- Parse arguments ---
for arg in "$@"; do
  case "$arg" in
    --name=*) SERVER_NAME="${arg#--name=}" ;;
    --type=*) SERVER_TYPE="${arg#--type=}" ;;
    --location=*) LOCATION="${arg#--location=}" ;;
    --ssh-key=*) SSH_KEY_PATH="${arg#--ssh-key=}" ;;
    --ssl) OPEN_SSL_PORTS=1 ;;
    --skip-server) SKIP_SERVER=1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT_DIR="$(dirname "$SCRIPTS_ROOT")"

if [ -z "$SERVER_NAME" ]; then
  echo "ERROR: --name is required"
  echo "Usage: scripts/infra/init-vps.sh --name=sea-staging"
  exit 1
fi

# --- Check prerequisites ---
for cmd in hcloud ssh-keygen; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' not found. Install it first."
    exit 1
  fi
done

if ! hcloud context active &>/dev/null; then
  echo "ERROR: No active hcloud context. Set one with: hcloud context use <name>"
  exit 1
fi
echo "Using hcloud context: $(hcloud context active)"

echo "============================================"
echo "  Init VPS: $SERVER_NAME"
echo "============================================"
echo "  Type:     $SERVER_TYPE"
echo "  Location: $LOCATION"
echo "  SSH key:  $SSH_KEY_PATH"
echo ""

# --- Step 1: SSH key ---
echo "=== 1. SSH key ==="
if [ -f "$SSH_KEY_PATH" ]; then
  echo "[ok] SSH key already exists: $SSH_KEY_PATH"
else
  echo "Generating SSH key..."
  ssh-keygen -t ed25519 -f "$SSH_KEY_PATH" -N "" -C "$SSH_KEY_NAME"
  echo "[ok] SSH key created."
fi

# Upload to Hetzner (idempotent — skip if already exists)
if hcloud ssh-key describe "$SSH_KEY_NAME" &>/dev/null; then
  echo "[ok] SSH key '$SSH_KEY_NAME' already in Hetzner."
else
  hcloud ssh-key create --name "$SSH_KEY_NAME" --public-key-from-file "${SSH_KEY_PATH}.pub" 2>&1 || {
    # Key may exist under a different name (uniqueness_error) — that's fine
    if hcloud ssh-key list -o noheader | grep -q "$(ssh-keygen -lf "${SSH_KEY_PATH}.pub" | awk '{print $2}')"; then
      echo "[ok] SSH key already in Hetzner (different name)."
    else
      echo "ERROR: Failed to upload SSH key."
      exit 1
    fi
  }
fi
echo ""

# --- Step 2: Create server ---
echo "=== 2. Server ==="
if [ -n "$SKIP_SERVER" ]; then
  echo "[skip] --skip-server flag set."
  SERVER_IP=$(hcloud server ip "$SERVER_NAME" 2>/dev/null) || {
    echo "ERROR: Server '$SERVER_NAME' not found."
    exit 1
  }
elif hcloud server describe "$SERVER_NAME" &>/dev/null; then
  echo "[ok] Server '$SERVER_NAME' already exists."
  SERVER_IP=$(hcloud server ip "$SERVER_NAME")
else
  echo "Creating server..."
  hcloud server create \
    --name "$SERVER_NAME" \
    --type "$SERVER_TYPE" \
    --image ubuntu-24.04 \
    --location "$LOCATION" \
    --ssh-key "$SSH_KEY_NAME"
  SERVER_IP=$(hcloud server ip "$SERVER_NAME")
  echo "[ok] Server created."
fi
echo "  IP: $SERVER_IP"

# Clear any stale host key for this IP (common when IP is recycled)
ssh-keygen -f "$HOME/.ssh/known_hosts" -R "$SERVER_IP" 2>/dev/null || true

# Wait for SSH to become available (always — server may have just been created)
echo "Waiting for SSH..."
for i in $(seq 1 30); do
  if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -i "$SSH_KEY_PATH" "root@$SERVER_IP" "true" 2>/dev/null; then
    echo "[ok] SSH is up."
    break
  fi
  if [ "$i" = "30" ]; then
    echo "ERROR: SSH not available after 60s."
    exit 1
  fi
  sleep 2
done
echo ""

# --- Step 3: Firewall ---
echo "=== 3. Firewall ==="
FW_NAME="${SERVER_NAME}-fw"
if hcloud firewall describe "$FW_NAME" &>/dev/null; then
  echo "[ok] Firewall '$FW_NAME' already exists."
else
  hcloud firewall create --name "$FW_NAME"
  hcloud firewall add-rule "$FW_NAME" --direction in --protocol tcp --port 22 \
    --source-ips 0.0.0.0/0 --source-ips ::/0 --description SSH
  hcloud firewall add-rule "$FW_NAME" --direction in --protocol tcp --port 8080 \
    --source-ips 0.0.0.0/0 --source-ips ::/0 --description WP
  hcloud firewall add-rule "$FW_NAME" --direction in --protocol tcp --port 8081 \
    --source-ips 0.0.0.0/0 --source-ips ::/0 --description OJS
  echo "[ok] Firewall created with SSH + WP + OJS rules."
fi

if [ -n "$OPEN_SSL_PORTS" ]; then
  # Add 80/443 if not already present (idempotent — hcloud errors if duplicate)
  hcloud firewall add-rule "$FW_NAME" --direction in --protocol tcp --port 80 \
    --source-ips 0.0.0.0/0 --source-ips ::/0 --description HTTP 2>/dev/null || true
  hcloud firewall add-rule "$FW_NAME" --direction in --protocol tcp --port 443 \
    --source-ips 0.0.0.0/0 --source-ips ::/0 --description HTTPS 2>/dev/null || true
  echo "[ok] SSL ports (80/443) open."
fi

# Apply firewall to server (idempotent)
hcloud firewall apply-to-resource "$FW_NAME" --type server --server "$SERVER_NAME" 2>/dev/null || true
echo ""

# --- Step 4: SSH verify ---
# Scripts resolve IP via hcloud at runtime — no SSH config needed.
echo "=== 4. SSH verify ==="
ssh-keygen -f "$HOME/.ssh/known_hosts" -R "$SERVER_IP" 2>/dev/null || true
if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -i "$SSH_KEY_PATH" "root@$SERVER_IP" "true" 2>/dev/null; then
  echo "[ok] SSH verified (root@$SERVER_IP)."
else
  echo "[warn] SSH connection failed."
fi
echo ""

# --- Step 5: Update .env files with new IP ---
echo "=== 5. Env files ==="
# Update any .env.staging / .env.prod files that reference the old IP
for ENV_CANDIDATE in "$PROJECT_DIR/.env.staging" "$PROJECT_DIR/.env.prod"; do
  if [ -f "$ENV_CANDIDATE" ]; then
    if grep -qE "://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+" "$ENV_CANDIDATE"; then
      OLD_ENV_IP=$(grep -oE "[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+" "$ENV_CANDIDATE" | head -1)
      if [ "$OLD_ENV_IP" != "$SERVER_IP" ]; then
        sed -i "s/$OLD_ENV_IP/$SERVER_IP/g" "$ENV_CANDIDATE"
        echo "[ok] $(basename "$ENV_CANDIDATE"): updated IP $OLD_ENV_IP -> $SERVER_IP"
      else
        echo "[ok] $(basename "$ENV_CANDIDATE"): IP already correct."
      fi
    else
      echo "[skip] $(basename "$ENV_CANDIDATE"): no IP found to update."
    fi
  fi
done
echo ""

# --- Done ---
echo "============================================"
echo "  VPS ready: $SERVER_NAME ($SERVER_IP)"
echo "============================================"
echo ""
echo "Next steps:"
echo "  1. Create .env:"
echo "     cp .env.example .env.staging"
echo "     # Edit .env.staging: set URLs, passwords, API keys"
echo ""
echo "  2. Deploy:"
echo "     scripts/infra/deploy.sh --host=$SERVER_NAME --provision --env-file=.env.staging"
echo ""
echo "  3. Verify:"
echo "     scripts/monitoring/smoke-test.sh --host=$SERVER_NAME"
