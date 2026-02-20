#!/bin/bash
# Environment variable coverage validation
# Ensures all vars used in compose files are documented in .env.example

check_env_vars() {
    local errors=0
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || repo_root="."

    # Find all compose files
    local compose_files=()
    for f in "$repo_root"/docker-compose*.yml; do
        [[ -f "$f" ]] && compose_files+=("$f")
    done

    if [[ ${#compose_files[@]} -eq 0 ]]; then
        echo "    SKIP: No compose files found"
        return 0
    fi

    # Extract all ${VAR} and ${VAR:-default} patterns from compose files
    local compose_vars=""
    for file in "${compose_files[@]}"; do
        local vars
        vars=$(grep -oE '\$\{[A-Z_][A-Z0-9_]*' "$file" 2>/dev/null | sed 's/\${//' | sort -u)
        compose_vars+="$vars"$'\n'
    done
    compose_vars=$(echo "$compose_vars" | sort -u | grep -v '^$')

    # Check .env.example exists
    local env_example="$repo_root/.env.example"
    if [[ ! -f "$env_example" ]]; then
        echo "    ERROR: .env.example not found"
        return 1
    fi

    # Extract vars from .env.example (including commented examples)
    local env_example_vars
    env_example_vars=$(grep -E '^[# ]*[A-Z_][A-Z0-9_]*=' "$env_example" 2>/dev/null | \
        sed -E 's/^[# ]*([A-Z_][A-Z0-9_]*)=.*/\1/' | sort -u)

    # Find missing vars
    local missing=""
    while IFS= read -r var; do
        [[ -z "$var" ]] && continue
        if ! echo "$env_example_vars" | grep -qx "$var"; then
            missing+="      - $var"$'\n'
        fi
    done <<< "$compose_vars"

    if [[ -n "$missing" ]]; then
        echo "    ERROR: Variables used in compose files but missing from .env.example:"
        echo "$missing"
        echo "    Fix: Add these to .env.example with placeholder values"
        errors=1
    fi

    return $errors
}
