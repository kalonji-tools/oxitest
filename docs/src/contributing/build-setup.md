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

## Using bacon

[bacon](https://dystroy.org/bacon/) is a background code checker that watches source files and
re-runs commands on change. It is included in the Nix devshell.

Start it with the default job (runs oxitest tests):

```console
$ bacon
```

Switch jobs with `-j`:

```console
$ bacon -j clippy
$ bacon -j ruff
```

Available jobs:

| Job | What it does |
|-----|-------------|
| `oxitest` (default) | Runs `just test` — builds the extension and runs the Python test suite |
| `ruff` | Lints Python code with ruff |
| `ty` | Type-checks Python code with ty |
| `clippy` | Lints Rust code with clippy |
| `test-rust` | Runs Rust unit tests |
| `fmt` | Checks Python formatting with ruff |

bacon watches the relevant source directories and re-runs automatically when files change.
Press `h` inside bacon for keyboard shortcuts.

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

## Repository layout (bare repo + worktrees)

The oxitest repository uses a **bare git repo** (`oxitest/`) with worktrees for each
branch. The main worktree is `oxitest.main/`; feature branches live in sibling
directories like `oxitest.<branch>/`.

[worktrunk](https://github.com/kalonji-tools/worktrunk) (`wt`) manages worktrees:

```bash
wt switch --create my-feature -y    # create a worktree for a new branch
wt remove                           # remove the current worktree after merge
```

Git hooks are shared across all worktrees via `core.hooksPath`, which points to the
bare repo's hooks directory. This means pre-commit and pre-push hooks work in every
worktree without extra setup.

**Why worktrees?** They let you work on multiple branches concurrently without
switching — each branch gets its own directory, build artifacts, and virtual
environment.
