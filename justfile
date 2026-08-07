# Apply Nix environment workarounds only when inside a Nix shell.
# Outside Nix these expand to empty strings and have no effect.

# Nix injects Python paths into PYTHONPATH and _PYTHON_SYSCONFIGDATA_NAME,
# corrupting sysconfig.EXT_SUFFIX and causing maturin to name the .so with
# the wrong ABI tag. Unset both before any maturin/python invocation.
fix_env := if env("IN_NIX_SHELL", "") != "" {
    "unset _PYTHON_SYSCONFIGDATA_NAME PYTHONPATH &&"
} else { "" }

# maturin internally calls `uv pip install`; without VIRTUAL_ENV set, uv
# follows the venv python's symlink back to the immutable Nix store and fails.
# Point VIRTUAL_ENV at the project venv (devenv sets UV_PROJECT_ENVIRONMENT,
# falls back to .venv outside devenv).
venv_dir := env("UV_PROJECT_ENVIRONMENT", justfile_directory() / ".venv")

maturin_env := if env("IN_NIX_SHELL", "") != "" {
    "VIRTUAL_ENV=" + venv_dir
} else { "" }

# ── Color codes for log output ───────────────────────────────────────────────
_green := "32"
_red := "31"
_yellow := "33"
_blue := "34"

# ── Recipes ──────────────────────────────────────────────────────────────────

# Show available recipes
default:
    @just --list

[private]
_log color msg:
    @printf '\033[{{color}}m→ %s\033[0m\n' '{{msg}}'

# Build the Rust extension
build *args: (_log _green "Building extension...")
    {{fix_env}} uv sync --group build
    {{maturin_env}} maturin develop {{args}}

# Run Python tests (no rebuild — use `just build` first if Rust changed)
test-python *args: (_log _blue "Running Python tests...")
    uv run python -m oxitest {{args}}

# Run Rust unit tests (matches CI: rejects unreferenced snapshots)
test-rust *args: (_log _blue "Running Rust tests...")
    cargo insta test --unreferenced=reject {{args}}

# Run all static checks (format, lint, clippy, spelling)
check: (_log _blue "Running static checks...")
    ruff format --check
    cargo fmt --check
    ruff check
    ty check
    cargo clippy --all-targets -- -D warnings
    codespell --toml pyproject.toml

# Validate lock files match manifests (matches prek pre-push hooks)
check-locks: (_log _blue "Checking lock files...")
    uv lock --check
    cargo metadata --locked --format-version 1 --quiet > /dev/null

# A mutant applied over uncommitted work is destroyed with that work by the
# `git checkout -- <file>` that reverts it. That revert cannot touch untracked
# files, so untracked content is not a reason to refuse — refusing on it made
# this gate's verdict depend on which worktree you stood in (#1939). Gitignored
# scratch was already invisible to `git status --porcelain`.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Refuse uncommitted tracked changes before applying a mutant (#1925, #1939)
mutation-guard:
    @if [ -n "$(git status --porcelain --untracked-files=no)" ]; then \
        just _log {{ _red }} "Dirty tree — a mutant applied now dies with the work it sits on:"; \
        git status --porcelain --untracked-files=no; \
        exit 1; \
    fi; \
    just _log {{ _green }} "Clean baseline @ $(git rev-parse HEAD^{tree})"

# The applier lives in scripts/apply_mutant.py, which documents the anchor
# contract and the exit codes this recipe maps. Every terminal state below
# exits explicitly: a mid-recipe failure does not propagate on its own
# (measured — `false` followed by an `echo` exits 0), which is how a void run
# reads as a pass.
#
# Only the line directly above a recipe becomes its `just --list` description.
# Run one mutant end to end: apply, build, test, revert (#1939)
mutate path old new *test_cmd: mutation-guard
    #!/usr/bin/env bash
    set -uo pipefail

    if ! git ls-files --error-unmatch '{{ path }}' > /dev/null 2>&1; then
        just _log {{ _red }} "MUTANT NOT APPLIED — {{ path }} is not tracked, so the revert would have nothing to restore"
        exit 1
    fi

    python3 scripts/apply_mutant.py '{{ path }}' '{{ old }}' '{{ new }}'
    applied=$?

    # 9 is the applier's own "this mutant cannot be applied"; anything else
    # non-zero is the applier failing for a reason of its own, which is void.
    if [ "$applied" -eq 9 ]; then
        just _log {{ _red }} "MUTANT NOT APPLIED — tree untouched"
        exit 1
    elif [ "$applied" -ne 0 ]; then
        just _log {{ _red }} "VOID: scripts/apply_mutant.py failed (exit $applied) — 127 means python3 is not on PATH, so run inside the devenv shell; 2 means it could not read an anchor, and <old>/<new> are file paths rather than inline text. The docstring in that script documents the rest."
        exit 2
    fi

    just _log {{ _blue }} "Mutant applied:"
    git --no-pager diff -- '{{ path }}'

    just build
    built=$?
    if [ "$built" -ne 0 ]; then
        # No rebuild here: a failed build installed nothing, so the extension on
        # disk is still the pre-mutant one.
        git checkout -- '{{ path }}'
        just _log {{ _red }} "VOID: BUILD FAILED (exit $built) — no test result can be read from a stale binary; mutant reverted"
        exit 2
    fi

    test_cmd='{{ test_cmd }}'
    [ -z "$test_cmd" ] && test_cmd='just test-python'
    just _log {{ _blue }} "Testing the mutant with: $test_cmd"
    eval "$test_cmd"
    tested=$?

    git checkout -- '{{ path }}'
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        just _log {{ _red }} "REVERT INCOMPLETE — tracked changes remain after reverting {{ path }}:"
        git status --porcelain --untracked-files=no
        exit 4
    fi

    # The source is honest again but the built extension still holds the mutant,
    # so a later bare test run would fail with no cause anywhere in the tree.
    just build
    rebuilt=$?
    if [ "$rebuilt" -ne 0 ]; then
        just _log {{ _red }} "BINARY STILL MUTATED — source reverted but the rebuild failed (exit $rebuilt); run 'just build' before trusting any later test run"
        exit 5
    fi

    if [ "$tested" -ne 0 ]; then
        just _log {{ _green }} "KILLED (test command exit $tested) — confirm it is the failure you predicted"
        exit 0
    fi
    just _log {{ _yellow }} "SURVIVED — the suite passed with the mutant applied. This is a finding, not a pass."
    exit 3

# Full pre-push gate: clean, check, test everything
preflight: clean check-locks check test-rust build test-python
    @just _log {{_blue}} "Running doc example tests..."
    uv run python -m oxitest python/tests/docs/ --strict=off
    @just _log {{_blue}} "Building docs (strict)..."
    uv run --group docs mkdocs build --strict
    mdbook build docs/internals
    cargo doc --no-deps --document-private-items
    @just _log {{_green}} "Preflight passed"

# Format code and fix typos
fmt *args: (_log _yellow "Formatting...")
    ruff format {{args}} python/
    cargo fmt {{args}}
    codespell --toml pyproject.toml --write-changes

# Build all documentation sites
docs-build: (_log _green "Building all docs...")
    uv run --group docs mkdocs build
    mdbook build docs/internals
    cargo doc --no-deps --document-private-items

# Serve docs with live reload (hot-reload on save, but cross-discipline links 404)
docs-serve: (_log _green "Starting doc servers...")
    -cargo doc --no-deps --document-private-items
    uv run --group docs mkdocs serve --dev-addr localhost:8000 &
    mdbook serve docs/internals --port 3000 &
    python3 -m http.server 3001 --directory target/doc &
    @just _log {{_green}} "User docs:      http://localhost:8000"
    @just _log {{_green}} "Internals book: http://localhost:3000"
    @just _log {{_green}} "Rust API docs:  http://localhost:3001/_oxitest"
    @just _log {{_green}} "Stop with: just docs-stop"

# Serve all docs from a single origin (cross-links work, no live reload)
docs-unified: docs-build (_log _green "Starting unified doc server...")
    python3 -m http.server 9000 --directory docs &
    @just _log {{_green}} "All docs:       http://localhost:9000/index.html"
    @just _log {{_green}} "User docs:      http://localhost:9000/site/"
    @just _log {{_green}} "Internals:      http://localhost:9000/internals/book/"
    @just _log {{_green}} "Architecture:   http://localhost:9000/internals/architecture-map.html"
    @just _log {{_green}} "Stop with: just docs-stop"

# Stop all background doc servers
docs-stop: (_log _red "Stopping doc servers...")
    -pkill -f "mkdocs serve"
    -pkill -f "mdbook serve"
    -pkill -f "http.server 3001"
    -pkill -f "http.server 9000"

# Remove build artifacts
clean: (_log _red "Removing build artifacts...")
    cargo clean
    rm -f python/oxitest/_oxitest*.so

# Run hyperfine benchmarks
bench: (_log _blue "Running benchmarks...")
    bash benchmarks/run.sh

# Print speedup summary against baseline
bench-compare: (_log _blue "Comparing benchmarks...")
    python benchmarks/compare.py

# Check that all required tools are on PATH
health:
    #!/usr/bin/env bash
    missing=0
    for cmd in cargo uv maturin ruff ty mkdocs mdbook codespell python3; do
        if command -v "$cmd" > /dev/null 2>&1; then
            printf '  ✓ %s (%s)\n' "$cmd" "$(command -v "$cmd")"
        else
            printf '  ✗ %s NOT FOUND\n' "$cmd"
            missing=$((missing + 1))
        fi
    done
    if [ "$missing" -gt 0 ]; then
        printf '\n%d tool(s) missing\n' "$missing"
        exit 1
    else
        printf '\nAll tools available\n'
    fi

    printf '\n'
    just agent-health

# Check that required agent skills are installed (warnings only)
agent-health:
    #!/usr/bin/env bash
    skills_file="docs/agents/required-skills.txt"
    if [ ! -f "$skills_file" ]; then
        printf '  SKIP: %s not found\n' "$skills_file"
        exit 0
    fi

    found_agent=0
    for agent in claude codex; do
        if command -v "$agent" > /dev/null 2>&1; then
            printf '  \033[32m✓\033[0m %s (%s)\n' "$agent" "$(command -v "$agent")"
            found_agent=1
        fi
    done

    if [ "$found_agent" -eq 0 ]; then
        printf '  No agent detected — skipping skill checks\n'
        exit 0
    fi

    missing=0
    checked=0
    if command -v claude > /dev/null 2>&1; then
        printf '\n  Claude Code skills:\n'
        while IFS= read -r skill || [ -n "$skill" ]; do
            skill=$(echo "$skill" | sed 's/#.*//' | xargs)
            [ -z "$skill" ] && continue
            checked=$((checked + 1))
            if echo "$skill" | grep -q ':'; then
                plugin=$(echo "$skill" | cut -d: -f1)
                if ls -d ~/.claude/plugins/cache/*/"${plugin}" > /dev/null 2>&1; then
                    printf '    \033[32m✓\033[0m %s\n' "$skill"
                else
                    printf '    \033[33mWARN\033[0m %s not installed (plugin: %s)\n' "$skill" "$plugin"
                    missing=$((missing + 1))
                fi
            else
                if [ -e ~/.claude/skills/"$skill" ]; then
                    printf '    \033[32m✓\033[0m %s\n' "$skill"
                else
                    printf '    \033[33mWARN\033[0m %s not installed\n' "$skill"
                    missing=$((missing + 1))
                fi
            fi
        done < "$skills_file"
    fi

    if [ "$checked" -eq 0 ]; then
        printf '\n  No skill checks available for detected agent(s)\n'
    elif [ "$missing" -gt 0 ]; then
        printf '\n  %d skill(s) missing\n' "$missing"
    else
        printf '\n  All required skills available\n'
    fi
