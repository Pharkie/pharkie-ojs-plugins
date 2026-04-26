#!/usr/bin/env bash
# Clone the private repo and symlink its env files into the workspace.
# Called from devcontainer postCreateCommand. Non-fatal on missing gh auth
# or missing env files — logs what happened and moves on.
set -u

if [ -d private/.git ]; then
  echo "setup-private: private/ already cloned, skipping clone"
elif ! command -v gh >/dev/null 2>&1; then
  echo "setup-private: gh CLI not found, skipping private repo setup"
  exit 0
elif ! gh auth status >/dev/null 2>&1; then
  echo "setup-private: gh not authenticated, skipping private repo setup"
  echo "setup-private: run 'gh auth login' and rerun scripts/dev/setup-private.sh"
  exit 0
else
  if ! gh repo clone Pharkie/sea-ojs-private private; then
    echo "setup-private: clone failed (check gh auth and repo access)"
    exit 0
  fi
fi

for env_file in .env.live .env.staging; do
  if [ -f "private/$env_file" ]; then
    ln -sf "private/$env_file" "$env_file"
    echo "setup-private: linked $env_file -> private/$env_file"
  else
    echo "setup-private: private/$env_file not present, skipping"
  fi
done
