#!/bin/bash
# Check for broken internal links in markdown documentation
# Returns errors for broken file references and anchor links

source "$(dirname "${BASH_SOURCE[0]}")/common.sh"

# Convert a markdown heading to its GitHub anchor ID
# e.g., "## Step 4: Configure Each App" -> "step-4-configure-each-app"
_heading_to_anchor() {
    local heading="$1"
    echo "$heading" \
        | sed 's/^#* *//' \
        | tr '[:upper:]' '[:lower:]' \
        | sed 's/[^a-z0-9 _-]//g' \
        | sed 's/ /-/g'
}

# Extract all anchors (heading IDs) from a markdown file
_get_file_anchors() {
    local file="$1"
    grep -E '^#{1,6} ' "$file" 2>/dev/null | while IFS= read -r line; do
        _heading_to_anchor "$line"
    done
}

# The primary checkout's root. In a linked worktree (e.g. .claude/worktrees/<name>)
# this is the checkout that owns the .git directory; otherwise it is the repo root.
_primary_root() {
    local common
    common=$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || { get_repo_root; return; }
    dirname "$common"
}

# A target is external when it leaves the repo (a sibling repo such as
# ../membership-platform) or sits under a gitignored top-level directory
# (private/). Prints the root that must exist for the target to be checkable:
# "../membership-platform" or "private". Prints nothing for in-repo targets.
_external_root() {
    local check_file="$1" first
    if [[ "$check_file" == ../* ]]; then
        local rest="${check_file#../}"
        echo "../${rest%%/*}"
        return
    fi
    first="${check_file%%/*}"
    [[ "$first" == "$check_file" ]] && return
    if git check-ignore -q "$first/" 2>/dev/null; then
        echo "$first"
    fi
}

check_doc_links() {
    local repo_root primary_root errors skipped
    repo_root=$(get_repo_root)
    primary_root=$(_primary_root)
    errors=0
    skipped=0

    # Find all markdown files tracked by git
    local md_files
    md_files=$(git ls-files '*.md' 2>/dev/null | grep -v '^node_modules/')

    if [[ -z "$md_files" ]]; then
        echo "    SKIP: No markdown files found"
        return 0
    fi

    while IFS= read -r md_file; do
        [[ -z "$md_file" ]] && continue

        local file_dir
        file_dir=$(dirname "$md_file")

        local in_code_block=false
        local line_num=0
        while IFS= read -r line; do
            ((line_num++))

            # Track fenced code blocks
            if [[ "$line" == '```'* ]]; then
                if $in_code_block; then
                    in_code_block=false
                else
                    in_code_block=true
                fi
                continue
            fi
            $in_code_block && continue

            # Extract link targets
            local targets
            targets=$(echo "$line" | grep -o ']([^)]*)' 2>/dev/null | sed 's/^]//' | sed 's/)$//' | sed 's/^(//')
            [[ -z "$targets" ]] && continue

            while IFS= read -r target; do
                [[ -z "$target" ]] && continue

                # Skip external links
                case "$target" in
                    http://*|https://*|mailto:*) continue ;;
                esac

                # Skip image references
                case "$target" in
                    *.png|*.jpg|*.jpeg|*.gif|*.svg|*.ico|*.webp) continue ;;
                esac

                # Split target into file path and anchor
                local target_file="" target_anchor=""
                if [[ "$target" == *"#"* ]]; then
                    target_file="${target%%#*}"
                    target_anchor="${target#*#}"
                else
                    target_file="$target"
                fi

                # Skip empty targets
                [[ -z "$target_file" && -z "$target_anchor" ]] && continue

                # Only check .md file links (skip .sh, .yml, .env, etc.)
                if [[ -n "$target_file" ]]; then
                    case "$target_file" in
                        *.md) ;; # Check these
                        *) continue ;; # Skip non-markdown
                    esac
                fi

                # Determine the full path to check
                local check_file
                if [[ -z "$target_file" ]]; then
                    check_file="$md_file"
                else
                    check_file="$file_dir/$target_file"
                    check_file=$(python3 -c "import os.path; print(os.path.normpath('$check_file'))" 2>/dev/null || echo "$check_file")
                fi

                # In-repo targets resolve against this checkout. External ones
                # (sibling repo, private/) resolve against the primary checkout,
                # and are skipped when their repo is not on this machine.
                local base="$repo_root"
                if [[ -n "$target_file" ]]; then
                    local ext_root
                    ext_root=$(_external_root "$check_file")
                    if [[ -n "$ext_root" ]]; then
                        base="$primary_root"
                        if [[ ! -e "$base/$ext_root" ]]; then
                            echo "    SKIP: $md_file:$line_num — '$target_file' ($ext_root is not on this machine)"
                            skipped=$((skipped + 1))
                            continue
                        fi
                    fi
                    if [[ ! -f "$base/$check_file" ]]; then
                        echo "    ERROR: $md_file:$line_num — broken link to '$target_file' (file not found)"
                        errors=$((errors + 1))
                        continue
                    fi
                fi

                # Check if anchor exists in target file
                if [[ -n "$target_anchor" ]]; then
                    local anchors
                    anchors=$(_get_file_anchors "$base/$check_file")
                    if ! echo "$anchors" | grep -qFx -- "$target_anchor"; then
                        echo "    ERROR: $md_file:$line_num — broken anchor '#$target_anchor' in '$(basename "$check_file")'"
                        errors=$((errors + 1))
                    fi
                fi
            done <<< "$targets"
        done < "$repo_root/$md_file"
    done <<< "$md_files"

    if [[ $errors -eq 0 ]]; then
        echo "    OK: All internal doc links valid"
        [[ $skipped -gt 0 ]] && echo "    ($skipped link(s) into repos not on this machine skipped)"
    fi

    return $(( errors > 0 ))
}
