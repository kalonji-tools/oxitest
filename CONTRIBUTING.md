# Contributing to oxitest

Thanks for your interest in contributing! This guide covers the essentials.
For deeper topics, links point to the full [documentation site](https://kalonji-tools.github.io/oxitest/).

## Quick start

oxitest uses [Nix](https://nixos.org/) for a reproducible dev environment. With Nix installed:

```bash
nix develop          # shell with Rust, Python 3.12, maturin, uv, all dev tools
just health          # verify toolchain
just dev             # full cycle: Rust tests, build extension, Python tests
```

Without Nix, install Rust (stable), Python 3.12+, and maturin manually. See
[Build Setup](https://kalonji-tools.github.io/oxitest/contributing/build-setup/)
for details. Note that PyO3 linking can be tricky outside Nix — you may need to
set `PYO3_PYTHON` and `LIBRARY_PATH` explicitly.

### Useful commands

```bash
just build           # compile the Rust extension (required before Python tests)
just test            # run Python tests (auto-rebuilds)
just test-rust       # run Rust unit tests
just lint            # ruff + ty type check
just fmt             # format Python + Rust
just docs-serve      # local docs site with live reload
```

## Submitting changes

1. **Branch from `main`.**
2. **Follow [Conventional Commits](https://www.conventionalcommits.org/)**:
   `feat: add X`, `fix: correct Y`, `refactor: simplify Z`, `docs: update W`.
3. **Include tests** for new behaviour — Rust unit tests in the relevant module,
   Python tests in `python/tests/`.
4. **Run `just dev`** before pushing. Pre-commit hooks auto-fix formatting;
   pre-push hooks run clippy and lock-file validation.
5. **Open a PR** against `main`. Fill in the PR template (description, linked
   issue, checklist).

### What must pass before merge

- All CI jobs green: code quality, tests (Python 3.11/3.12/3.13), coverage
- PR template checklist completed
- At least one maintainer approval

## Testing

**Rust unit tests** are inline in their modules (`#[cfg(test)]`):

```bash
just test-rust                          # all Rust tests
just test-rust test_mtime               # single test by name
```

**Python tests** live in `python/tests/`:

```bash
just test                               # all Python tests
just test python/tests/test_fixtures.py # single file
```

**Integration tests** live in the separate
[oxitest-consumer](https://github.com/kalonji-tools/oxitest-consumer) repository.
They run oxitest against a real test suite and verify end-to-end behaviour. See
that repo's README for setup.

## Architecture overview

oxitest is a two-layer system:

- **Rust** (`src/`) orchestrates the pipeline: config, file discovery, filtering,
  scheduling, parallel dispatch, caching, and reporting.
- **Python** (`python/oxitest/_bridge/`) handles test execution: fixture injection,
  mark evaluation, assertion rewriting, and result building.
- The layers communicate via **PyO3**. Key data contracts (`TestResult`,
  `CollectedItem`) must stay in sync across languages.

See the full [Architecture](https://kalonji-tools.github.io/oxitest/contributing/architecture/)
guide for the pipeline diagram and module reference.

## Code style

- **Rust**: `cargo fmt` + `clippy` (all warnings denied). No `unsafe` code — enforced
  by `unsafe_code = "forbid"` in `Cargo.toml`.
- **Python**: `ruff` (lint + format) + `ty` (type checker). Type hints on all public APIs.
- **Commits**: Conventional Commits. Pre-commit hooks auto-fix formatting so you
  rarely need to think about style.

## Labels

Every issue and pull request should carry at least one label from each relevant
group below. The `priority` and `size` labels are set by maintainers; the
`area` label should be set by the author when opening an issue or PR.

### Type labels

| Label | When to use |
|---|---|
| `bug` | Something is broken or behaves unexpectedly |
| `enhancement` | New feature or improvement to existing behaviour |
| `documentation` | Docs-only change: README, CONTRIBUTING, inline comments |
| `good first issue` | Self-contained, well-scoped — good entry point for new contributors |
| `help wanted` | Maintainer would welcome external contribution |
| `question` | Needs clarification before work can begin |

### Priority labels (set by maintainers)

| Label | Meaning |
|---|---|
| `priority: high` | Must be resolved in this milestone |
| `priority: medium` | Should be resolved in this milestone |
| `priority: low` | Nice to have; can slip |

### Size labels (set by maintainers)

| Label | Meaning |
|---|---|
| `size: S` | Small — a few hours |
| `size: M` | Medium — a day or two |
| `size: L` | Large — several days |
| `size: XL` | Extra large — a week or more |

### Area labels

| Label | Meaning |
|---|---|
| `area: ci` | CI/CD pipeline |
| `area: cli` | CLI interface and flags |
| `area: docs` | Documentation site or content |
| `area: python-bridge` | Python bridge layer |
| `area: release` | Packaging and release |
| `area: rust-core` | Rust source code |
| `area: security` | Security |
| `area: tooling` | Developer tooling and repo setup |
