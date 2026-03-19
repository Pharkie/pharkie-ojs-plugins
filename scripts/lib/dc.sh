#!/bin/bash
# Docker Compose wrapper that handles DinD (devcontainer) vs regular Docker.
#
# Source this in any script that needs to call docker compose:
#   source "$(dirname "$0")/lib/dc.sh"
#   init_dc [--env=dev|staging|prod] [--ssl]
#
# After init_dc, use $DC everywhere:
#   $DC up -d
#   $DC exec -T wp bash ...
#
# How it works:
#   DinD (devcontainer): volume mounts resolve against the HOST filesystem,
#     so --project-directory must be the host path. Detected via HOST_PROJECT_DIR
#     env var set in devcontainer.json.
#   Regular Docker (VPS): compose files are relative to the current directory.

# Detect DinD: HOST_PROJECT_DIR is set in devcontainer.json → containerEnv.
# It expands to the host-side workspace path (e.g. /Users/adam/dev/SEA/wp-ojs-sync).
is_dind() {
  [ -n "${HOST_PROJECT_DIR:-}" ]
}

# Build the $DC variable. Call once at the top of your script.
# Args:
#   --env=dev|staging|prod  (default: auto-detect from DinD)
#   --ssl                   (include Caddy overlay)
init_dc() {
  local env="" ssl=""
  for arg in "$@"; do
    case "$arg" in
      --env=*) env="${arg#--env=}" ;;
      --ssl) ssl=1 ;;
    esac
  done

  # Auto-detect: if HOST_PROJECT_DIR is set, we're in DinD → dev
  if [ -z "$env" ]; then
    if is_dind; then
      env="dev"
    else
      echo "ERROR: init_dc requires --env= when not in devcontainer" >&2
      return 1
    fi
  fi

  local repo_dir
  repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

  case "$env" in
    dev)
      if is_dind; then
        # DinD: --project-directory is the HOST path (for volume mount resolution).
        # -f and --env-file use CONTAINER paths (where the files actually are).
        DC="docker compose --project-directory $HOST_PROJECT_DIR -f $repo_dir/docker-compose.yml --env-file $repo_dir/.env"
      else
        # Not DinD but env=dev (e.g. native Linux with Docker)
        DC="docker compose -f $repo_dir/docker-compose.yml --env-file $repo_dir/.env"
      fi
      ;;
    staging|prod)
      local compose_files="-f docker-compose.yml -f docker-compose.staging.yml"
      # Detect Caddy overlay (either from --ssl flag or already running)
      if [ -n "$ssl" ] || docker compose $compose_files -f docker-compose.caddy.yml ps --status running 2>/dev/null | grep -q caddy; then
        compose_files="$compose_files -f docker-compose.caddy.yml"
      fi
      DC="docker compose $compose_files"
      ;;
    *)
      echo "ERROR: Unknown environment '$env'. Use dev, staging, or prod." >&2
      return 1
      ;;
  esac

  export DC
}
