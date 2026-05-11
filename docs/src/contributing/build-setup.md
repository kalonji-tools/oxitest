# Build Setup

!!! abstract "Contributing"
    How to set up the development environment for oxitest.

## Nix (recommended)

oxitest uses Nix for reproducible development environments. With Nix installed, a single command
gives you a shell with the complete toolchain — Rust toolchain, Python 3.12, maturin, uv, ruff,
ty, mkdocs-material, and pre-commit hooks.

```console
$ nix develop
```

The shell hook prints active versions and installs git hooks via `prek`.

### Build the Rust extension

```bash
maturin develop
```

Compiles the Rust extension and installs it into the current Python environment in-place. Run
this after any change to Rust source code before running Python-level tests.

### Run Rust unit tests

```bash
cargo test
```

### Build the docs

```bash
mkdocs build
```

Rendered HTML is written to `docs/site/`. To serve the docs locally with live reload:

```bash
mkdocs serve --dev-addr localhost:8000
```

## Non-Nix

Without Nix, install the following manually.

**Rust (stable):**

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

Verify: `rustc --version`

**Python:** 3.12 or later. Verify: `python3 --version`

**maturin:**

```bash
pip install maturin
```

**Build the extension:**

```bash
maturin develop
```

**Run Rust unit tests:**

```bash
cargo test
```

Outside Nix, maturin detects your system Python automatically. Set `PYO3_PYTHON` and
`LIBRARY_PATH` explicitly if you need to target a specific interpreter.
