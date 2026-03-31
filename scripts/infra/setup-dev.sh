#!/bin/bash
# Backwards-compatible shim — delegates to the unified setup script.
# Usage: scripts/infra/setup-dev.sh [--with-sample-data]
exec "$(dirname "$0")/setup.sh" --env=dev "$@"
