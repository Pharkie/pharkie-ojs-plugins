#!/bin/bash
# Resolve SSH connection details for a Hetzner server.
# Source this file, then call resolve_ssh <server-name>.
#
# Sets these variables:
#   SERVER_IP   - IPv4 address
#   SSH_CMD     - full ssh command prefix (e.g., "ssh -o ... root@1.2.3.4")
#   SCP_CMD     - full scp command prefix (e.g., "scp -o ... ")
#   SCP_HOST    - user@ip prefix for scp destinations (e.g., "root@1.2.3.4")
#   RSYNC_SSH   - ssh command for rsync -e (e.g., "ssh -o ... -i ...")
#
# IP resolution order:
#   1. SERVER_IP env var (pre-set, e.g. in CI)
#   2. hcloud CLI lookup (requires active context)
#
# SSH key: defaults to ~/.ssh/hetzner, override with second arg or SSH_KEY env var.

resolve_ssh() {
  local server_name="$1"
  local ssh_key="${2:-${SSH_KEY:-$HOME/.ssh/hetzner}}"

  if [ -z "$server_name" ]; then
    echo "ERROR: resolve_ssh requires a server name"
    exit 1
  fi

  # If SERVER_IP is already set (e.g. by CI), skip hcloud lookup
  if [ -z "$SERVER_IP" ]; then
    if ! command -v hcloud &>/dev/null; then
      echo "ERROR: SERVER_IP not set and hcloud CLI not found."
      echo "       Either set SERVER_IP env var or install hcloud."
      exit 1
    fi

    if ! hcloud context active &>/dev/null; then
      echo "ERROR: No active hcloud context. Set one with: hcloud context use <name>"
      exit 1
    fi

    SERVER_IP=$(hcloud server ip "$server_name" 2>/dev/null) || {
      echo "ERROR: Server '$server_name' not found in hcloud context '$(hcloud context active)'."
      echo "       Available servers: $(hcloud server list -o noheader -o columns=name 2>/dev/null || echo '(none)')"
      exit 1
    }
  fi

  local ssh_user="deploy"
  local ssh_opts="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -o ControlMaster=auto -o ControlPath=/tmp/ssh-%r@%h:%p -o ControlPersist=5m -i $ssh_key"
  SSH_CMD="ssh $ssh_opts $ssh_user@$SERVER_IP"
  SCP_CMD="scp $ssh_opts"
  SCP_HOST="$ssh_user@$SERVER_IP"
  RSYNC_SSH="ssh $ssh_opts"
}
