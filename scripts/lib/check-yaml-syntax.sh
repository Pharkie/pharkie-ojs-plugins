#!/bin/bash
# YAML syntax validation for compose files
# Uses python (usually available) or basic checks as fallback

check_yaml_syntax() {
    local errors=0
    local repo_root
    repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || repo_root="."

    # Get staged YAML files
    local staged_yaml
    staged_yaml=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E '\.ya?ml$')

    if [[ -z "$staged_yaml" ]]; then
        echo "    SKIP: No YAML files staged"
        return 0
    fi

    # Check if PyYAML is available
    local has_pyyaml=false
    if python3 -c "import yaml" 2>/dev/null; then
        has_pyyaml=true
    fi

    if $has_pyyaml; then
        for file in $staged_yaml; do
            [[ -f "$repo_root/$file" ]] || continue
            if ! python3 -c "import yaml; yaml.safe_load(open('$repo_root/$file'))" 2>/dev/null; then
                echo "    ERROR: Invalid YAML syntax in $file"
                python3 -c "import yaml; yaml.safe_load(open('$repo_root/$file'))" 2>&1 | head -3 | sed 's/^/      /'
                ((errors++))
            fi
        done
    else
        # Fallback: check for tabs (YAML requires spaces)
        for file in $staged_yaml; do
            [[ -f "$repo_root/$file" ]] || continue
            if grep -qP '^\t' "$repo_root/$file" 2>/dev/null; then
                echo "    ERROR: Tab characters found in $file (YAML requires spaces)"
                ((errors++))
            fi
        done
        echo "    NOTE: Install PyYAML for full validation: pip install pyyaml"
    fi

    return $errors
}
