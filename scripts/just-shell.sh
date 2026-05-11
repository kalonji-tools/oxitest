#!/usr/bin/env bash
# Used by justfile as `set shell`.
# If already inside the Nix dev shell, run commands directly.
# Otherwise, enter it first via `nix develop`.
if [ -n "$IN_NIX_SHELL" ]; then
    exec bash "$@"
else
    exec nix develop --command bash "$@"
fi
