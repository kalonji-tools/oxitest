# Apply Nix environment workarounds only when inside a Nix shell.
# Outside Nix these expand to empty strings and have no effect.

# Nix injects Python 3.13 paths into PYTHONPATH and _PYTHON_SYSCONFIGDATA_NAME,
# corrupting sysconfig.EXT_SUFFIX for the 3.12 env and causing maturin to name
# the .so with the wrong ABI tag. Unset both before any maturin/python invocation.
fix_env := if env("IN_NIX_SHELL", "") != "" {
    "unset _PYTHON_SYSCONFIGDATA_NAME PYTHONPATH &&"
} else { "" }

# maturin internally calls `uv pip install`; without VIRTUAL_ENV set, uv follows
# .venv/bin/python's symlink back to the immutable Nix store and fails.
# Pointing VIRTUAL_ENV at the project venv keeps uv scoped to it.
maturin_env := if env("IN_NIX_SHELL", "") != "" {
    "VIRTUAL_ENV=" + justfile_directory() + "/.venv"
} else { "" }

# Build the Rust extension; extra args are forwarded to maturin
build *args:
    {{fix_env}} uv sync --group build
    {{maturin_env}} maturin develop {{args}}

# Run Python tests (rebuilds extension first)
test *args: build
    PYTHONPATH=python uv run python -m oxitest {{args}}

# Run only tests affected by uncommitted changes (or vs a given ref)
test-affected *args:
    just test --affected {{args}}

# Run Rust unit tests
test-rust *args:
    cargo test {{args}}

# Clean, run Rust tests, build extension, run Python tests
dev: clean test-rust test

# Lint Python (ruff) and check types (ty)
lint:
    ruff check python/
    ty check

# Format Python (ruff) and Rust (cargo fmt); pass --check to verify only
fmt *args:
    ruff format {{args}} python/
    cargo fmt {{args}}

# Build the documentation site
docs:
    mkdocs build

# Serve the documentation locally with live reload
docs-serve:
    mkdocs serve --dev-addr localhost:8000

# Run hyperfine benchmarks across all tiers
bench:
    bash bench/run.sh

# Print speedup summary against bench/baseline.json
bench-compare:
    python bench/compare.py

# Remove build artifacts
clean:
    cargo clean
    rm -f python/oxitest/_oxitest*.so

# Check that all required tools are on $PATH
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
