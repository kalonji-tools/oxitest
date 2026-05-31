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

[private]
_log color msg:
    @printf '\033[{{color}}m→ %s\033[0m\n' '{{msg}}'

# ── Recipes ──────────────────────────────────────────────────────────────────

# Show available recipes
help:
    @just --list

# Build the Rust extension
build *args: (_log _green "Building extension...")
    {{fix_env}} uv sync --group build
    {{maturin_env}} maturin develop {{args}}

# Build extension and run Python tests
test *args: build
    @just _log {{_blue}} "Running Python tests..."
    PYTHONPATH=python uv run python -m oxitest {{args}}

# Run Python tests without rebuilding
test-py *args: (_log _blue "Running Python tests (skip build)...")
    PYTHONPATH=python uv run python -m oxitest {{args}}

# Run tests affected by uncommitted changes
test-affected *args: (_log _blue "Running affected tests...")
    just test --affected {{args}}

# Run Rust unit tests
test-rust *args: (_log _blue "Running Rust tests...")
    cargo test {{args}}

# Clean, run Rust tests, build, run Python tests
dev: clean test-rust test

# Lint Python (ruff) and check types (ty)
lint: (_log _blue "Linting Python...")
    ruff check python/
    ty check

# Format Python (ruff) and Rust (cargo fmt)
fmt *args: (_log _yellow "Formatting code...")
    ruff format {{args}} python/
    cargo fmt {{args}}

# Build the documentation site
docs: (_log _green "Building docs...")
    mkdocs build

# Serve docs locally with live reload
docs-serve: (_log _blue "Serving docs at localhost:8000...")
    mkdocs serve --dev-addr localhost:8000

# Run hyperfine benchmarks
bench: (_log _blue "Running benchmarks...")
    bash bench/run.sh

# Print speedup summary against baseline
bench-compare: (_log _blue "Comparing benchmarks...")
    python bench/compare.py

# Remove build artifacts
clean: (_log _red "Removing build artifacts...")
    cargo clean
    rm -f python/oxitest/_oxitest*.so

# Check that all required tools are on PATH
health:
    #!/usr/bin/env bash
    missing=0
    for cmd in cargo uv maturin ruff ty mkdocs codespell python3; do
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
