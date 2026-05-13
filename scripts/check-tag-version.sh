#!/usr/bin/env bash
# Pre-push hook: when pushing a v* tag, verify it matches the version in Cargo.toml.
# Git passes one line per ref being pushed via stdin:
#   <local ref> <local sha1> <remote ref> <remote sha1>
set -euo pipefail

while IFS=' ' read -r _local_ref local_sha remote_ref _remote_sha; do
    if [[ "$remote_ref" != refs/tags/v* ]]; then
        continue
    fi

    tag="${remote_ref#refs/tags/v}"
    cargo_version=$(git show "$local_sha:Cargo.toml" | grep -m1 '^version = ' | sed 's/version = "\(.*\)"/\1/')

    if [[ "$tag" != "$cargo_version" ]]; then
        echo "ERROR: tag v${tag} does not match Cargo.toml version \"${cargo_version}\"."
        echo "  Bump the version in Cargo.toml before tagging."
        exit 1
    fi
done

exit 0
