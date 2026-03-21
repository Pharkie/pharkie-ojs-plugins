#!/bin/bash
# Secret detection for pre-commit hook
# Scans all tracked files for accidental secret exposure

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

check_secrets() {
    local errors=0

    local files_to_check
    files_to_check=$(get_files_to_scan)

    if [[ -z "$files_to_check" ]]; then
        return 0
    fi

    # FAIL FAST: .env files should NEVER be committed (they contain secrets).
    # .env.example and .env.staging.example are templates (no secrets) — allow those.
    for file in $files_to_check; do
        case "$file" in
            .env.example|.env.staging.example) ;; # templates are OK
            .env*) echo "    ERROR: Environment file '$file' is staged for commit. These files contain secrets and must be gitignored."; ((errors++)) ;;
        esac
    done

    for file in $files_to_check; do
        # Skip binary files, env example files, and check scripts themselves
        case "$file" in
            *.png|*.jpg|*.gif|*.ico|*.woff|*.woff2|*.ttf|*.eot|*.svg) continue ;;
            .env*) continue ;;
            scripts/lib/check-*.sh) continue ;;
            scripts/lib/common.sh) continue ;;
            *.md) continue ;;
        esac

        local content
        content=$(read_file_content "$file") || continue

        # Pattern 1: PEM private key blocks
        if echo "$content" | grep -qE '^-----BEGIN (RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----' 2>/dev/null; then
            echo "    ERROR: Private key block detected in $file"
            ((errors++))
        fi

        # Pattern 2: Generic high-entropy secrets (long base64 in value position)
        if echo "$content" | grep -qE '(PASSWORD|SECRET|API_KEY)=[A-Za-z0-9+/=]{30,}$' 2>/dev/null; then
            local match
            match=$(echo "$content" | grep -oE '(PASSWORD|SECRET|API_KEY)=[A-Za-z0-9+/=]{30,}$')
            if ! echo "$match" | grep -qiE '(your|here|example|placeholder|xxx|dev)'; then
                echo "    WARNING: Possible secret value in $file"
                ((errors++))
            fi
        fi

        # Pattern 3: Bearer/Auth tokens in non-example files
        if echo "$content" | grep -qE '(Authorization|Bearer|TOKEN):\s*(Bearer\s+)?[A-Za-z0-9._-]{20,}' 2>/dev/null; then
            local match
            match=$(echo "$content" | grep -oE '(Authorization|Bearer|TOKEN):\s*(Bearer\s+)?[A-Za-z0-9._-]{20,}')
            if ! echo "$match" | grep -qiE '(your|here|example|placeholder|xxx)'; then
                echo "    ERROR: Possible auth token in $file"
                ((errors++))
            fi
        fi

        # Pattern 4: WordPress auth salts (real values, not placeholders)
        if echo "$content" | grep -qE '(AUTH_KEY|SECURE_AUTH_KEY|LOGGED_IN_KEY|NONCE_KEY|AUTH_SALT|SECURE_AUTH_SALT|LOGGED_IN_SALT|NONCE_SALT)=.{30,}' 2>/dev/null; then
            local match
            match=$(echo "$content" | grep -oE '(AUTH_KEY|SECURE_AUTH_KEY|LOGGED_IN_KEY|NONCE_KEY|AUTH_SALT|SECURE_AUTH_SALT|LOGGED_IN_SALT|NONCE_SALT)=.{30,}')
            if ! echo "$match" | grep -qiE '(your|here|example|placeholder|xxx|dev-key|change)'; then
                echo "    ERROR: Possible WordPress auth salt in $file"
                ((errors++))
            fi
        fi

        # Pattern 5: Passwords 15+ chars (non-placeholder)
        if echo "$content" | grep -qE '(_PASSWORD|_PASSWD)=[^[:space:]]{15,}' 2>/dev/null; then
            local match
            match=$(echo "$content" | grep -oE '(_PASSWORD|_PASSWD)=[^[:space:]]{15,}')
            if ! echo "$match" | grep -qiE '(your|here|example|placeholder|xxx|\$\{|\$[A-Z])'; then
                echo "    WARNING: Possible password in $file"
                ((errors++))
            fi
        fi

        # Pattern 6: Hetzner API tokens (64-char alphanumeric, often in HCLOUD_TOKEN= or hcloud context)
        if echo "$content" | grep -qE 'HCLOUD_TOKEN="[A-Za-z0-9]{60,}"' 2>/dev/null; then
            local match
            match=$(echo "$content" | grep -oE 'HCLOUD_TOKEN="[A-Za-z0-9]{60,}"')
            if ! echo "$match" | grep -qiE '(paste|your|placeholder|REDACTED)'; then
                echo "    ERROR: Hetzner API token detected in $file"
                ((errors++))
            fi
        fi
    done

    # Also scan .md files for API tokens (not covered by main loop which skips .md)
    local md_files
    md_files=$(echo "$files_to_check" | grep '\.md$' || true)
    for file in $md_files; do
        local content
        content=$(read_file_content "$file") || continue
        if echo "$content" | grep -qE 'HCLOUD_TOKEN="[A-Za-z0-9]{60,}"' 2>/dev/null; then
            local match
            match=$(echo "$content" | grep -oE 'HCLOUD_TOKEN="[A-Za-z0-9]{60,}"')
            if ! echo "$match" | grep -qiE '(paste|your|placeholder|REDACTED)'; then
                echo "    ERROR: Hetzner API token detected in $file"
                ((errors++))
            fi
        fi
    done

    return $errors
}
