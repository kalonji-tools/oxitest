set shell := ["./scripts/just-shell.sh", "-c"]

# Nix injects Python 3.13 paths into PYTHONPATH and _PYTHON_SYSCONFIGDATA_NAME,
# corrupting sysconfig.EXT_SUFFIX for the 3.12 env and causing maturin to name
# the .so with the wrong ABI tag. Unset both before any maturin/python invocation.
fix_env := "unset _PYTHON_SYSCONFIGDATA_NAME PYTHONPATH"

# maturin internally calls `uv pip install`; without VIRTUAL_ENV set, uv follows
# .venv/bin/python's symlink back to the immutable Nix store and fails.
# Pointing VIRTUAL_ENV at the project venv keeps uv scoped to it.
maturin_env := "VIRTUAL_ENV=$(pwd)/.venv"

# Build the Rust extension; extra args are forwarded to maturin
build *args:
    {{fix_env}} \
    && uv sync --group build \
    && {{maturin_env}} maturin develop {{args}}

# Run Python tests (rebuilds extension first)
test *args:
    {{fix_env}} \
    && uv sync --group test \
    && {{maturin_env}} maturin develop \
    && PYTHONPATH=python uv run python -m oxitest {{args}}

# Run Rust unit tests
test-rust *args:
    cargo test {{args}}

# Clean, run Rust tests, build extension, run Python tests — all in one shell
dev:
    cargo clean \
    && rm -f python/oxitest/_oxitest*.so \
    && cargo test \
    && {{fix_env}} \
    && uv sync --group test \
    && {{maturin_env}} maturin develop \
    && PYTHONPATH=python uv run python -m oxitest

# Lint Python (ruff) and check types (ty)
lint:
    ruff check python/ \
    && ty check

# Format Python (ruff) and Rust (cargo fmt)
fmt:
    ruff format python/ \
    && cargo fmt

# Build the documentation site
docs:
    mkdocs build

# Serve the documentation locally with live reload
docs-serve:
    mkdocs serve --dev-addr localhost:8000

# Run all checks without modifying files
check:
    ruff check python/ \
    && ruff format --check python/ \
    && ty check \
    && cargo fmt --check \
    && cargo clippy -- -D warnings

# Remove build artifacts
clean:
    cargo clean \
    && rm -f python/oxitest/_oxitest*.so
