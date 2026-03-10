#!/bin/bash
# Resolve SSH connection details for a Hetzner server via hcloud CLI.
# Source this file, then call resolve_ssh <server-name>.
#
# Sets these variables:
#   SERVER_IP   - IPv4 address from hcloud
#   SSH_CMD     - full ssh command prefix (e.g., "ssh -o ... root@1.2.3.4")
#   SCP_CMD     - full scp command prefix (e.g., "scp -o ... ")
#   SCP_HOST    - user@ip prefix for scp destinations (e.g., "root@1.2.3.4")
#   RSYNC_SSH   - ssh command for rsync -e (e.g., "ssh -o ... -i ...")
#
# Requires: hcloud CLI with an active context, SSH key at ~/.ssh/hetzner

resolve_ssh() {
  local server_name="$1"
  local ssh_key="${2:-$HOME/.ssh/hetzner}"

  if [ -z "$server_name" ]; then
    echo "ERROR: resolve_ssh requires a server name"
    exit 1
  fi

  if ! command -v hcloud &>/dev/null; then
    echo "ERROR: hcloud CLI not found. Install it first."
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

  local ssh_opts="-o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new -i $ssh_key"
  SSH_CMD="ssh $ssh_opts root@$SERVER_IP"
  SCP_CMD="scp $ssh_opts"
  SCP_HOST="root@$SERVER_IP"
  RSYNC_SSH="ssh $ssh_opts"
}
