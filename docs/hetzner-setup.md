# Hetzner Cloud Setup

We use [Hetzner Cloud](https://www.hetzner.com/cloud/) for staging and production. Good value, EU data centres, straightforward API. `scripts/init-vps.sh` automates the full setup: SSH key, server creation, firewall, SSH config, and GitHub deploy key. After init, use [deploy.sh](vps-deployment.md) to deploy the stack.

## Recommended plan

**CPX22** — 3 vCPU (AMD), 4 GB RAM, 80 GB SSD. Runs the full stack comfortably with headroom for sync operations and traffic spikes. ~7 EUR/month.

## Quick start with init-vps.sh

```bash
# Requires hcloud CLI and HCLOUD_TOKEN env var
scripts/init-vps.sh --name=my-server

# With SSL ports (production)
scripts/init-vps.sh --name=my-server --ssl

# Custom server type or location
scripts/init-vps.sh --name=my-server --type=cpx32 --location=fsn1
```

## Manual hcloud commands (reference)

If you prefer to set up manually or need to manage existing servers:

```bash
# Upload SSH key
hcloud ssh-key create --name my-key --public-key-from-file ~/.ssh/my-key.pub

# Create server
hcloud server create --name my-server --type cpx22 --image ubuntu-24.04 \
  --location nbg1 --ssh-key my-key

# Rebuild server (wipes everything, fresh OS)
hcloud server rebuild --image ubuntu-24.04 my-server
```

## Good to know: SSH keys on rebuild

`hcloud server rebuild` does **not** re-inject SSH keys from your Hetzner account. Use `--user-data-from-file` with cloud-init to inject your key:

```bash
hcloud server rebuild --image ubuntu-24.04 \
  --user-data-from-file <(cat <<EOF
#cloud-config
ssh_authorized_keys:
  - $(cat ~/.ssh/my-key.pub)
EOF
) my-server
```

## Locations

| Code | Location |
|---|---|
| `nbg1` | Nuremberg, Germany |
| `fsn1` | Falkenstein, Germany |
| `hel1` | Helsinki, Finland |
| `ash` | Ashburn, USA |
| `hil` | Hillsboro, USA |
