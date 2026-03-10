#!/bin/bash
# Run pytest for backfill Python files when any are staged

check_backfill_tests() {
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || repo_root="."

    # Get staged backfill Python files
    local staged_backfill
    staged_backfill=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E '^backfill/.*\.py$')

    if [[ -z "$staged_backfill" ]]; then
        echo "    SKIP: No backfill Python files staged"
        return 0
    fi

    # Check if pytest is available
    if ! python3 -m pytest --version &>/dev/null; then
        echo "    ERROR: pytest not installed (pip install pytest)"
        return 1
    fi

    # Run pytest
    local output exit_code
    output=$(cd "$repo_root" && python3 -m pytest backfill/tests/ -q 2>&1)
    exit_code=$?

    if [[ $exit_code -ne 0 ]]; then
        echo "    ERROR: Backfill tests failed"
        echo "$output" | sed 's/^/      /'
        return 1
    fi

    return 0
}
