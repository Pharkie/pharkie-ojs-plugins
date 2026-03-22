#!/bin/bash
# Environment file consistency check
# Ensures content-bearing env vars that should be identical across environments
# actually match between .env, .env.staging, and .env.live.
#
# Only checks vars listed in CONSISTENT_VARS — these are journal metadata and
# config that must not drift between environments. Secrets, URLs, passwords
# etc. are intentionally different per environment and are NOT checked.

check_env_consistency() {
    local errors=0
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || repo_root="."

    # Vars that must have identical values across all env files
    local CONSISTENT_VARS=(
        OJS_JOURNAL_NAME
        OJS_JOURNAL_ACRONYM
        OJS_JOURNAL_ABBREVIATION
        OJS_JOURNAL_CONTACT_NAME
        OJS_JOURNAL_CONTACT_EMAIL
        OJS_JOURNAL_PUBLISHER
        OJS_JOURNAL_PUBLISHER_URL
        OJS_JOURNAL_PRINT_ISSN
        OJS_JOURNAL_ONLINE_ISSN
        OJS_JOURNAL_COUNTRY
        OJS_JOURNAL_DESCRIPTION
        OJS_JOURNAL_ABOUT
        OJS_JOURNAL_SUBSCRIPTION_INFO
        OJS_SUB_TYPES
        OJS_PURCHASE_ARTICLE_FEE
        OJS_PURCHASE_ISSUE_FEE
    )

    # Env files to compare (only check files that exist and are staged or already tracked)
    local env_files=()
    for f in "$repo_root"/.env "$repo_root"/.env.staging "$repo_root"/.env.live; do
        [[ -f "$f" ]] && env_files+=("$f")
    done

    if [[ ${#env_files[@]} -lt 2 ]]; then
        echo "    SKIP: Need at least 2 env files to compare (found ${#env_files[@]})"
        return 0
    fi

    # Use .env as the reference
    local reference="$repo_root/.env"
    if [[ ! -f "$reference" ]]; then
        echo "    SKIP: .env not found (reference file)"
        return 0
    fi

    # Extract a var's value from an env file (handles single-quoted, double-quoted, and unquoted)
    get_var() {
        local file="$1" var="$2"
        # Match VAR=value, VAR='value', VAR="value"
        local line
        line=$(grep -E "^${var}=" "$file" 2>/dev/null | head -1) || true
        if [[ -z "$line" ]]; then
            echo "__MISSING__"
            return
        fi
        # Strip the VAR= prefix
        local value="${line#*=}"
        # Strip surrounding quotes if present
        if [[ "$value" =~ ^\'(.*)\'$ ]]; then
            value="${BASH_REMATCH[1]}"
        elif [[ "$value" =~ ^\"(.*)\"$ ]]; then
            value="${BASH_REMATCH[1]}"
        fi
        echo "$value"
    }

    local missing=""
    local mismatched=""

    for var in "${CONSISTENT_VARS[@]}"; do
        local ref_val
        ref_val=$(get_var "$reference" "$var")

        # Skip if not set in reference
        [[ "$ref_val" == "__MISSING__" ]] && continue

        for f in "${env_files[@]}"; do
            [[ "$f" == "$reference" ]] && continue

            local other_val
            other_val=$(get_var "$f" "$var")
            local fname
            fname=$(basename "$f")

            if [[ "$other_val" == "__MISSING__" ]]; then
                missing+="      - $var missing from $fname\n"
                errors=1
            elif [[ "$ref_val" != "$other_val" ]]; then
                mismatched+="      - $var differs in $fname\n"
                mismatched+="        .env:    ${ref_val:0:80}$([ ${#ref_val} -gt 80 ] && echo '...')\n"
                mismatched+="        $fname: ${other_val:0:80}$([ ${#other_val} -gt 80 ] && echo '...')\n"
                errors=1
            fi
        done
    done

    if [[ -n "$missing" ]]; then
        echo "    ERROR: Content vars missing from env files:"
        echo -e "$missing"
    fi

    if [[ -n "$mismatched" ]]; then
        echo "    ERROR: Content vars differ between env files (should be identical):"
        echo -e "$mismatched"
    fi

    if [[ $errors -gt 0 ]]; then
        echo "    Fix: Update the env files so these values match across .env, .env.staging, and .env.live"
    fi

    return $errors
}
