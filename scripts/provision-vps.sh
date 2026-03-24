#!/bin/bash
# Bootstrap a fresh Ubuntu 24.04 VPS for WP-OJS staging/prod.
# Runs ON the VPS (piped via SSH from deploy.sh).
# Idempotent — safe to re-run.
set -eo pipefail

echo "=== Provisioning VPS ==="

# --- Docker ---
if command -v docker &>/dev/null; then
  echo "[ok] Docker already installed: $(docker --version)"
else
  echo "Installing Docker..."
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  echo "[ok] Docker installed: $(docker --version)"
fi

# --- App directory ---
mkdir -p /opt/pharkie-ojs-plugins
echo "[ok] /opt/pharkie-ojs-plugins ready."

echo "=== Provisioning complete ==="
