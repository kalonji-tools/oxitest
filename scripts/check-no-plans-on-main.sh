#!/usr/bin/env bash
# Pre-push hook: reject pushes to main that contain plans/specs files.
# Git passes one line per ref being pushed via stdin:
#   <local ref> <local sha1> <remote ref> <remote sha1>
set -euo pipefail

PLANS_DIRS=(
    "docs/superpowers/plans"
    "docs/superpowers/specs"
)

while IFS=' ' read -r _local_ref local_sha _remote_ref remote_ref; do
    if [[ "$remote_ref" != "refs/heads/main" ]]; then
        continue
    fi

    found=()
    for dir in "${PLANS_DIRS[@]}"; do
        while IFS= read -r f; do
            [[ -n "$f" ]] && found+=("$f")
        done < <(git ls-tree -r --name-only "$local_sha" -- "$dir" 2>/dev/null || true)
    done

    if [[ ${#found[@]} -gt 0 ]]; then
        echo "ERROR: plans/specs files must be deleted before pushing to main."
        printf '  %s\n' "${found[@]}"
        exit 1
    fi
done

exit 0
