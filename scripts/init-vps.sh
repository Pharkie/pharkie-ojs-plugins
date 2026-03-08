#!/bin/bash
# One-time VPS setup: create server, firewall, SSH config, deploy key.
# Runs FROM the devcontainer. Requires hcloud CLI and HCLOUD_TOKEN.
#
# Usage:
#   scripts/init-vps.sh --name=sea-staging                     # Create new server
#   scripts/init-vps.sh --name=sea-staging --ssl                # Also open ports 80/443
#   scripts/init-vps.sh --name=sea-staging --type=cpx22         # Custom server type
#   scripts/init-vps.sh --name=sea-staging --location=fsn1      # Custom location
#   scripts/init-vps.sh --name=sea-staging --skip-server        # Skip server creation (already exists)
#
# After this script completes, run:
#   1. Create .env from .env.example and edit it
#   2. scripts/deploy.sh --host=<name> --provision --env-file=.env.staging
set -eo pipefail

# --- Defaults ---
SERVER_NAME=""
SERVER_TYPE="cpx22"
LOCATION="nbg1"
SSH_KEY_PATH="$HOME/.ssh/hetzner"
SSH_KEY_NAME="hetzner"
OPEN_SSL_PORTS=""
SKIP_SERVER=""
GITHUB_REPO="Pharkie/wp-ojs-sync"

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

if [ -z "$SERVER_NAME" ]; then
  echo "ERROR: --name is required"
  echo "Usage: scripts/init-vps.sh --name=sea-staging"
  exit 1
fi

# --- Check prerequisites ---
for cmd in hcloud gh ssh-keygen; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: '$cmd' not found. Install it first."
    exit 1
  fi
done

if [ -z "$HCLOUD_TOKEN" ]; then
  echo "ERROR: HCLOUD_TOKEN not set. Export your Hetzner Cloud API token."
  exit 1
fi

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

# --- Step 4: SSH config ---
echo "=== 4. SSH config ==="
SSH_CONFIG="$HOME/.ssh/config"
if grep -q "^Host $SERVER_NAME$" "$SSH_CONFIG" 2>/dev/null; then
  echo "[ok] SSH config entry for '$SERVER_NAME' already exists."
else
  # Ensure file exists
  mkdir -p "$(dirname "$SSH_CONFIG")"
  touch "$SSH_CONFIG"
  chmod 600 "$SSH_CONFIG"

  cat >> "$SSH_CONFIG" << EOF

Host $SERVER_NAME
  HostName $SERVER_IP
  User root
  IdentityFile $SSH_KEY_PATH
  IdentitiesOnly yes
EOF
  echo "[ok] Added '$SERVER_NAME' to SSH config."
fi

# Clear stale known_hosts entry for this hostname (IP may have changed)
ssh-keygen -f "$HOME/.ssh/known_hosts" -R "$SERVER_NAME" 2>/dev/null || true
ssh-keygen -f "$HOME/.ssh/known_hosts" -R "$SERVER_IP" 2>/dev/null || true

# Verify SSH works (accept new host key)
if ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "$SERVER_NAME" "true" 2>/dev/null; then
  echo "[ok] SSH connection verified."
else
  echo "[warn] SSH connection failed — check config manually."
fi
echo ""

# --- Step 5: GitHub deploy key ---
echo "=== 5. GitHub deploy key ==="
SSH_CMD="ssh -o ConnectTimeout=10 $SERVER_NAME"

# Generate deploy key on VPS if it doesn't exist
DEPLOY_KEY_EXISTS=$($SSH_CMD "test -f /root/.ssh/deploy_key && echo yes || echo no")
if [ "$DEPLOY_KEY_EXISTS" = "yes" ]; then
  echo "[ok] Deploy key already exists on VPS."
else
  $SSH_CMD "ssh-keygen -t ed25519 -f /root/.ssh/deploy_key -N '' -C '${SERVER_NAME}-deploy'"
  echo "[ok] Deploy key generated on VPS."
fi

# Register with GitHub (replace if exists — server may have been recreated with new key)
DEPLOY_PUB=$($SSH_CMD "cat /root/.ssh/deploy_key.pub")
EXISTING_KEY_ID=$(gh repo deploy-key list --repo "$GITHUB_REPO" 2>/dev/null | grep "${SERVER_NAME}" | awk '{print $1}')
if [ -n "$EXISTING_KEY_ID" ]; then
  echo "$EXISTING_KEY_ID" | while read KEY_ID; do
    echo "y" | gh repo deploy-key delete "$KEY_ID" --repo "$GITHUB_REPO" 2>/dev/null || true
  done
  echo "[ok] Removed old deploy key from GitHub."
fi
echo "$DEPLOY_PUB" | gh repo deploy-key add - --repo "$GITHUB_REPO" --title "$SERVER_NAME"
echo "[ok] Deploy key registered on GitHub."

# Configure SSH on VPS to use deploy key for GitHub
$SSH_CMD "cat > /root/.ssh/config << SSHEOF
Host github.com
  IdentityFile /root/.ssh/deploy_key
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
SSHEOF
chmod 600 /root/.ssh/config"
echo "[ok] VPS SSH config set for GitHub."

# Verify
GH_AUTH=$($SSH_CMD "ssh -T git@github.com 2>&1 || true")
if echo "$GH_AUTH" | grep -q "successfully authenticated"; then
  echo "[ok] GitHub authentication verified."
else
  echo "[warn] GitHub auth check returned: $GH_AUTH"
fi
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
echo "     scripts/deploy.sh --host=$SERVER_NAME --provision --env-file=.env.staging"
echo ""
echo "  3. Verify:"
echo "     scripts/smoke-test.sh --host=$SERVER_NAME"
