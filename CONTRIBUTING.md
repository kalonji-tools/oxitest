# Contributing to oxitest

Thanks for your interest in contributing!

## Quick start

oxitest uses [devenv](https://devenv.sh/) for a reproducible dev environment:

```bash
devenv shell         # shell with Rust, Python 3.12, maturin, uv, all dev tools
just health          # verify toolchain
just dev             # full cycle: Rust tests, build extension, Python tests
```

Without Nix, install Rust (stable), Python 3.12+, and maturin manually.

## Submitting changes

1. **Branch from `main`.**
2. **Follow [Conventional Commits](https://www.conventionalcommits.org/):**
   `feat: add X`, `fix: correct Y`, `refactor: simplify Z`, `docs: update W`.
3. **Include tests** for new behaviour.
4. **Run `just dev`** before pushing.
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
just docs-internals-serve    # live reload at localhost:3000
```
