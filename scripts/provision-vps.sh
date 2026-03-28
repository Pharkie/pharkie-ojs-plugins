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

# --- SSH hardening ---
echo "--- SSH hardening ---"
SSHD_HARDENING="/etc/ssh/sshd_config.d/hardening.conf"
cat > "$SSHD_HARDENING" <<'SSHEOF'
# Managed by provision-vps.sh — do not edit manually
PasswordAuthentication no
PermitRootLogin prohibit-password
PermitEmptyPasswords no
MaxAuthTries 3
MaxSessions 3
ClientAliveInterval 300
ClientAliveCountMax 2
SSHEOF
# Restart SSH to pick up changes (safe — we're already connected via key auth)
# Ubuntu 24.04 uses ssh.service, older versions use sshd.service
systemctl restart ssh 2>/dev/null || systemctl restart sshd
echo "[ok] SSH hardened ($SSHD_HARDENING)."

# --- fail2ban ---
echo "--- fail2ban ---"
if command -v fail2ban-server &>/dev/null; then
  echo "[ok] fail2ban already installed."
else
  apt-get update -qq
  apt-get install -y -qq fail2ban
  echo "[ok] fail2ban installed."
fi
systemctl enable --now fail2ban
echo "[ok] fail2ban active."

# --- Unattended upgrades ---
echo "--- Unattended upgrades ---"
if dpkg -s unattended-upgrades &>/dev/null; then
  echo "[ok] unattended-upgrades already installed."
else
  apt-get update -qq
  apt-get install -y -qq unattended-upgrades
  echo "[ok] unattended-upgrades installed."
fi
# Enable non-interactively (idempotent)
echo 'APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";' > /etc/apt/apt.conf.d/20auto-upgrades
systemctl enable --now unattended-upgrades
echo "[ok] Unattended security upgrades enabled."

# --- Host firewall (ufw) ---
echo "--- Host firewall (ufw) ---"
if command -v ufw &>/dev/null; then
  echo "[ok] ufw already installed."
else
  apt-get update -qq
  apt-get install -y -qq ufw
  echo "[ok] ufw installed."
fi
# Allow required ports (idempotent — ufw skips duplicates)
ufw allow 22/tcp   comment 'SSH'        > /dev/null
ufw allow 80/tcp   comment 'HTTP'       > /dev/null
ufw allow 443/tcp  comment 'HTTPS'      > /dev/null
ufw allow 8080/tcp comment 'WP staging' > /dev/null
ufw allow 8081/tcp comment 'OJS staging' > /dev/null
# Enable (--force skips interactive prompt, idempotent if already active)
ufw --force enable
echo "[ok] ufw active (22, 80, 443, 8080, 8081 allowed)."

echo "=== Provisioning complete ==="
