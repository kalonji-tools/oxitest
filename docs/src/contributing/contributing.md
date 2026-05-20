# Contributing

!!! abstract "Contributing"
    How to run tests, submit changes, and follow project conventions for oxitest.

## Running Rust tests

```bash
cargo test
```

Rust unit tests are inline in their modules and require no additional setup beyond the steps in
[Build Setup](build-setup.md).

## Running integration tests

Integration tests live in the separate
[oxitest-consumer](https://github.com/kalonji-tools/oxitest-consumer) repository.
It depends on oxitest via a `uv` editable install, so changes to the local oxitest
source are picked up immediately.

Tests use **oxitest fixtures** (not pytest) to exercise the full pipeline — collection,
scheduling, parallel execution, caching, and reporting.

```bash
cd oxitest-consumer
uv sync                                        # install deps (editable oxitest)
.venv/bin/python -m oxitest tests/             # run the integration suite
```

See the oxitest-consumer README for additional details.

## Pre-commit hooks

Pre-commit hooks run automatically via [prek](https://github.com/kalonji-tools/prek)
on every commit and push. They handle formatting, linting, and lock-file validation.

If the `ty` hook fails with a version-mismatch error (e.g. the Nix-store copy of `ty`
differs from the `.venv` copy), use `--no-verify` on the commit:

```bash
git commit --no-verify -m "feat: your message"
```

This is a known environment issue, not a code problem. CI runs `ty` from `.venv`
and will pass correctly.

## Design specs and plans

Non-trivial changes follow a structured workflow:

1. **Issue** — describes the problem or feature request.
2. **Spec** — a design document written before implementation, exploring trade-offs and
   decisions. Specs live in `docs/superpowers/specs/` (gitignored, local only).
3. **Plan** — an implementation checklist derived from the spec, posted as a comment on the PR.
4. **PR** — implements the plan and closes the issue.

Specs are posted as comments on their GitHub issue so the design rationale is publicly
visible even though the files themselves are not committed. If you are picking up an
issue that already has a spec comment, read it before starting.

## Submitting changes

- Branch from `main`
- `cargo test` must pass before opening a PR
- Commit messages follow Conventional Commits style: `feat: add X`, `fix: correct Y`, `docs: update Z`
- Include Rust unit tests for new behavior where applicable
