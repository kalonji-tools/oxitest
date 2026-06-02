# Contributing

!!! abstract "Contributing"
    How to run tests, submit changes, and follow project conventions for oxitest.

## Running Rust tests

```bash
cargo test
```

Rust unit tests are inline in their modules and require no additional setup beyond the steps in
[Build Setup](build-setup.md).

## Common commands

The project uses a `justfile` for common tasks. Run `just help` to see all recipes.

```bash
just health       # check all required tools are on PATH
just build        # build the Rust extension (required before Python tests)
just test         # build + run Python tests
just test-rust    # run Rust unit tests
just lint         # lint Python + type-check with ty
just fmt          # format Python + Rust
just clean        # clean build artifacts
```

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

If a hook fails, investigate the root cause rather than bypassing it. The hooks
catch real issues (formatting, lint, type errors) that CI will also catch.

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

## Code style

### Python tests

- **No class-based tests.** Use standalone `def test_*()` functions. The only
  exception is a class that shares `@oxi.parametrize` parameters across all
  its methods.
- **Arrange, Act, Assert.** Every test should have three clear phases.
- **Dogfood oxitest features.** Prefer `oxi.raises()` over `try/except`,
  `TempDir` over `tempfile`, `Patcher` over `unittest.mock.patch`,
  `@oxi.parametrize` over copy-pasted test functions.
- **Import helpers from conftest.** Use `from conftest import helpers` then
  `helpers.common.<function>()`. Never `sys.path.insert`.

### Rust

- `unsafe_code = "forbid"` and `warnings = "deny"` in `Cargo.toml`.
- All clippy lints are hard errors (`clippy::all = "deny"`).

## Quality checks

Pre-commit and pre-push hooks run via [prek](https://github.com/kalonji-tools/prek).
To run all checks manually:

```bash
prek run --all-files
```

CI runs this in the `code-quality` job.

## Snapshot testing

Rust tests use [insta](https://insta.rs/) for snapshot testing. When updating
snapshots:

```bash
cargo insta test              # run tests and review new snapshots
cargo insta review            # interactively approve/reject changes
```

CI runs `cargo insta test --unreferenced=reject` to catch stale snapshots.

## Submitting changes

- Branch from `main`
- `cargo test` must pass before opening a PR
- Commit messages follow Conventional Commits style: `feat: add X`, `fix: correct Y`, `docs: update Z`
- Include Rust unit tests for new behavior where applicable

## Recommended reading

Start with [Architecture](architecture.md) for a map of the codebase before
diving into code.
