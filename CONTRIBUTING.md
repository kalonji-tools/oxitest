# Contributing to oxitest

Thanks for your interest in contributing!

## Quick start

oxitest uses [devenv](https://devenv.sh/) for a reproducible dev environment:

```bash
devenv shell         # shell with Rust, Python 3.12, maturin, uv, all dev tools
just health          # verify toolchain
just preflight       # full pre-push gate: clean, check, test-rust, build, test
```

### Without Nix

If you can't use devenv, install Rust (stable), Python 3.12+, and maturin
manually. Then copy `.env.example` → `.env` and edit as needed:

- **`VIRTUAL_ENV`** — path to your Python venv. `just build` needs this so `uv`
  can find the venv's `python` symlink (otherwise it follows the symlink into
  the immutable Nix store and fails).
- **`RUST_LOG`** — tracing verbosity (e.g. `RUST_LOG=oxitest=debug`). Optional.

Inside devenv, `.env` is not needed — Nix and `direnv` handle everything.

## Submitting changes

1. **Branch from `main`.**
2. **Follow [Conventional Commits](https://www.conventionalcommits.org/):**
   `feat: add X`, `fix: correct Y`, `refactor: simplify Z`, `docs: update W`.
3. **Include tests** for new behaviour.
4. **Run `just preflight`** before pushing.
5. **Open a PR** against `main`.

### What must pass before merge

- All CI jobs green
- PR template checklist completed
- At least one maintainer approval

## Internals book

For architecture, module map, pipeline internals, how to extend oxitest, and
testing strategy, see the
**[Internals Book](https://kalonji-tools.github.io/oxitest/internals/)**.

To serve it locally:

```bash
just docs-serve              # all docs at :8000, :3000, :3001
```
