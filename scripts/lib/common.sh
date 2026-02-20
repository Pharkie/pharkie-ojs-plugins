#!/bin/bash
# Shared functions for pre-commit checks
# Source this at the top of each check script

# Get repository root (cached for performance)
_REPO_ROOT=""
get_repo_root() {
    if [[ -z "$_REPO_ROOT" ]]; then
        _REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || _REPO_ROOT="."
    fi
    echo "$_REPO_ROOT"
}

# Get all tracked files in the repo
get_all_tracked_files() {
    git ls-files 2>/dev/null | grep -v '^\.env'
}

# Get staged files (for change-specific checks)
get_staged_files() {
    git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -v '^\.env'
}

# Combine staged and tracked files, deduplicated
get_files_to_scan() {
    local staged tracked
    staged=$(get_staged_files)
    tracked=$(get_all_tracked_files)
    echo -e "$staged\n$tracked" | sort -u | grep -v '^$'
}

# Read file content safely
read_file_content() {
    local file="$1"
    cat "$file" 2>/dev/null
}
