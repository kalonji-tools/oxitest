# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

oxitest is a Python test runner rewritten in Rust. It exposes a Python API (fixture system, marks, builtins) implemented in `python/oxitest/_bridge/`, and a Rust core that orchestrates collection, scheduling, parallel execution, caching, and reporting. The two halves communicate via PyO3.

## Commands

```bash
# Enter development shell (provides cargo, python, maturin, just)
devenv shell

# Check all required tools are available
just health

# Check required agent skills are installed (warnings only)
just agent-health

# Build the Rust extension (required before running Python tests)
just build

# Run Python tests (no rebuild — build first if Rust changed)
just test

# Run a single Python test file
just test python/tests/test_fixtures.py

# Run Rust unit tests
just test-rust

# Run a single Rust test
just test-rust <test_name>

# Run all static checks (format, lint, clippy, spelling)
just check

# Format code and fix typos
just fmt

# Full pre-push gate (clean + check + test-rust + build + test)
just preflight

# Clean build artifacts
just clean

# Show all available recipes
just
```

## Workflow

### New ideas → Grill → Issues → Spec → Plan → Implement → Merge

**1. Grill new ideas.** Any new feature, concept, or design direction MUST go through `grill-with-docs` before anything else. This ensures ideas are stress-tested against the existing domain model and documented decisions before committing to them.

**2. Create issues.** Once an idea survives grilling and is deemed worth implementing, create GitHub issues. Every issue MUST state the "why" — why is this change needed? What problem does it solve? Organize into milestones if the work spans multiple issues. Every issue MUST be triaged. Apply one **category label** (`bug` or `enhancement`) and one **component label** (`rust` or `python`) to each issue.

**3. Triage issues.** Every issue gets a **state label** reflecting its triage status. See `docs/agents/triage-labels.md` for the label vocabulary.

**4. Spec every issue.** By the time a PR is created, every issue in that PR MUST have a design spec. If no issue exists yet for the work being specced, create one first — every spec needs a home issue. Specs can be written when the issue is picked up or ahead of time — but never skipped. Use the `superpowers:brainstorming` skill for spec design. Post each issue's spec section as a comment on that issue. When issues share a grouped spec, post only the section relevant to each issue — not the entire spec on every issue.

**5. Create a draft PR.** Push the branch and open a draft PR before any implementation begins. This gives reviewers a chance to evaluate the approach early. Always assign the user to the PR on creation (`gh pr edit --add-assignee @me`).

**6. Plan before implementing.** Use the `superpowers:writing-plans` skill. Multiple issues can be grouped into one plan if they are tightly coupled or logically sequential. The plan MUST be posted as a comment on the PR — never on individual issues.

**7. Implement via subagents or inline.** Use `superpowers:subagent-driven-development` or `superpowers:executing-plans`.

**8. Post-implementation review.** After all plan tasks are implemented and pushed, run these passes before marking the PR ready:
- **`ponytail:ponytail-review`** on the branch diff — hunt over-engineering, dead code, and unnecessary complexity.
- **`/improve branch`** — audit the branch changes for correctness, security, test coverage gaps, and tech debt.
- **Explore findings before acting.** Present findings to the user. For each finding, explore the cited code to verify it's real and determine if the fix is safe. Only fix after exploration confirms the finding is actionable. Never blindly apply review suggestions.
- **Docs evaluation.** Check whether the changes affect user-facing documentation. Scan `docs/user/`, `docs/internals/`, `CONTEXT.md`, and error references for stale content. If docs need updating, fix them in the same PR — don't let stale docs ship.

**9. Merge rules.**
- **Never push directly to main.** All changes go through pull requests.
- **Never merge without approval.** Wait for either a GitHub review approval or an explicit user command (e.g., "merge", "merge rebase delete branch"). Do not auto-merge after CI passes.
- Only `--rebase` merge is allowed. Never squash merge, never merge commits.
- Every commit message title MUST include its related issue number: `feat: add Foo (#42)`
- Multiple issues per commit are fine: `feat: add Bar and Baz (#43, #44)`
- **PR closing keywords**: GitHub requires the keyword before EACH issue number. Write `Closes #1, Closes #2, Closes #3` — NOT `Closes #1, #2, #3` (only the first gets closed).
- **Pre-merge commit hygiene**: When a merge is triggered (e.g., "merge", "merge rebase delete branch"), evaluate the commit history first. If commits are too granular or disorganized, logically rebase them into coherent commits before merging. Each commit should represent a logical unit of work.
- Run `just preflight` before pushing.

**10. Post-merge debrief.** After a PR is merged, if the implementation diverged from the plan, add a debrief comment to the closed PR explaining how, where, and why it diverged. Apply the `diverged-from-plan` label to the PR. This label is only applied to closed/merged PRs.

### Quick reference

| Stage | Required? | Skill | Labels |
|-------|-----------|-------|--------|
| Grill new ideas | Always | `grill-with-docs` | — |
| Create issues | Always | — | category (`bug`/`enhancement`) + component (`rust`/`python`) |
| Triage issues | Always | `triage` | See `docs/agents/triage-labels.md` |
| Design spec | Before PR | `superpowers:brainstorming` | — |
| Draft PR | Before coding | — | — |
| Implementation plan | Before coding | `superpowers:writing-plans` | — |
| Execute plan | During coding | `superpowers:subagent-driven-development` | — |
| Ponytail review | After push | `ponytail:ponytail-review` | — |
| Improve audit | After push | `/improve branch` | — |
| Code review | Before merge | `superpowers:requesting-code-review` | — |
| Post-merge debrief | If diverged | — | `diverged-from-plan` (closed PRs only) |

## Tools

### Worktrunk (`wt`)

All branch management uses Worktrunk. Never use raw `git checkout` or `git branch` for feature work.

```bash
# Create a new worktree for a feature branch
wt switch --create <branch>

# Switch to an existing worktree
wt switch <branch>
```

Worktrunk runs `direnv reload` on switch (`post-switch` hook), which activates the devenv shell automatically. This means all tools (`cargo`, `ruff`, `just`, `prek`, etc.) are on PATH immediately — no manual nix store path hunting.

### devenv

The development environment is managed by devenv. All commands assume you are inside the devenv shell.

```bash
# Enter manually (if not using wt)
devenv shell

# Load into current shell without subshell
eval "$(devenv print-dev-env)"
```

Never install tools globally or via `pip install` / `cargo install`. If a tool is missing, add it to `devenv.nix`.

### prek

Pre-commit hooks are managed by prek (not pre-commit). Hooks run automatically on `git commit`. To run all hooks manually:

```bash
prek run --all-files
```

## Architecture

### Two-layer design

**Rust layer** (`src/`): Entry point is `src/lib.rs`, which exposes a single `run(args)` PyO3 function. The Rust layer handles:
- `config.rs` — CLI parsing (clap) and `pyproject.toml` config under `[tool.oxitest]`
- `collector.rs` — file discovery based on `testpaths`/`python_files` patterns
- `cache.rs` — timing cache for parallel scheduling decisions and `--lf`/`--ff` support
- `filter.rs` — query DSL (`-E`) filtering, `--lf`/`--ff`, grouping by module
- `query/` — query DSL compiler, evaluator, and `oxitest query` subcommand
- `parallel.rs` — spawns worker subprocesses; each worker runs `python/oxitest/_bridge/worker.py`
- `scheduler.rs` — distributes test groups across workers
- `reporter/` — TTY, CI, and JSON (CTRF) reporters
- `strict.rs` — strict-mode violation checking (bare asserts, dict parametrize, missing mark reason)
- `bridge.rs` — PyO3 calls into the Python bridge: `collect_module`, `run_test`, `FixtureSession`

**Python bridge** (`python/oxitest/_bridge/`): Pure-Python layer that does the actual test execution. Key modules by responsibility:

*Fixture system:*
- `_fixture_registry.py` — `FixtureDef`, `FixtureRegistry`; fixture definition and registry
- `_fixture_session.py` — `FixtureSession`, `_SessionProtocol`, `_Scope`; fixture lifecycle (scope caching, yield teardown, autouse)
- `_fixture_context.py` — fixture resolution context
- `_fixture_instantiator.py` — fixture instantiation and dependency injection
- `_fixture_type.py` — `Fixture[T]`, `FixtureRef[T]`, `Yields[T]` type aliases
- `_fixture_validator.py` — fixture signature and type validation
- `proxy.py` / `proxy_ns.py` — `FrozenProxy` (shared fixtures) and `FixturesProxy` (namespace-aware `fx: Fixtures` injection)
- `_builtins/` — built-in injectable fixtures: `TempDir`, `TempDirFactory`, `StdCapture`, `FdCapture`, `Patcher`, `LogCapture`, `TestContext`

*Mark system:*
- `_mark_api.py` — mark evaluation: skip, xfail, timeout, and custom marks
- `_mark_registry.py` — mark registration and custom mark definitions

*Helper system:*
- `_helpers.py` — `Helpers` registry class and `HelperDef`
- `_helper_registry.py` — helper namespace resolution and access

*Plugin system:*
- `plugin_loader.py` — plugin import, validation, `PluginRegistry` (frozen dataclass), `_PluginRegistryBuilder`
- `_plugin_config.py` — plugin settings resolution

*Execution:*
- `executor.py` — `run_test()`: loads module, resolves fixtures/parametrize, runs test, returns `TestResult`
- `_runners.py` — test execution runners (serial, debug)
- `result.py` — `TestResult` and outcome types
- `worker.py` — entry point for parallel worker subprocesses; reads JSON tasks from stdin, writes results to stdout
- `parametrize.py` — resolves `@mark.parametrize` kwargs into per-case values

*Collection:*
- `importer.py` — `collect_module()`: imports test file, discovers `test_*` functions, returns `CollectedItem` list
- `conftest_loader.py` — loads `conftest.py` files, registers their `Fixtures()` and `Helpers()` instances
- `_loader.py` — module loading infrastructure

*Infrastructure:*
- `_coverage.py` — coverage provider integration (`CoveragePyProvider`)
- `_debugger.py` — debugger backend integration
- `_fn_metadata.py` — `FunctionMetadata` frozen dataclass
- `_violation_checkers.py` — strict-mode violation checking
- `_namespace_validation.py` — fixture/helper namespace validation
- `_assert_error.py` — `_OxitestAssertionError` and enriched assertion diagnostics

### PyO3 data contract

Both the serial PyO3 path (`bridge.rs`) and the parallel JSON path (`worker_result/`) converge on `RawOutcome` (in `worker_result/convert.rs`) before producing a `TestOutcome`. `CollectedItem` fields must stay in sync with the Python `collect_module` return type. When adding fields to the Python result objects, update the corresponding `RawOutcome` variant and the PyO3 extraction logic in `bridge.rs`.

### Parallel execution

The Rust scheduler spawns `python -m oxitest._bridge.worker` subprocesses. Each worker receives a JSON task (module path + items + conftest paths) via stdin and writes one JSON result line per test to stdout. The worker is persistent within a run — it processes tasks until stdin is closed.

### Fixture injection protocol

Parameters annotated with `Fixture[T]` are injected; unannotated parameters are NOT (except built-in types like `TempDir`, `TestContext` which carry their own injection marker). `FixtureRef[T]` is for fixture references inside `@mark.parametrize` kwargs. `Fixtures` (bare, not `Fixture[T]`) injects a `FixturesProxy` namespace accessor.

### Configuration

`[tool.oxitest]` in `pyproject.toml` controls: `testpaths`, `python_files`, `norecursedirs`, `markers`, `timeout`, `cache_max_age`, `min_parallel_tests`, `timeout_multiplier`, `spawn_overhead_ms`, `strict`. All CLI flags override pyproject values.

### Type checking

`ty check` is the project's type checker. It runs on `python/oxitest/` via `just check`.

## Testing

- **Rust unit tests** (`just test-rust`): Unit tests for Rust modules.
- **Python integration tests** (`just test`): Run real commands. Tests use oxitest itself as the runner (`strict = "abort"`).
- **CI**: GitHub Actions. Two parallel jobs: `check` (static analysis via `just check`) and `test` (`just test-rust`, `just build`, `just test`). Uses `dtolnay/rust-toolchain`, `astral-sh/setup-uv`, `Swatinem/rust-cache` — no devenv in CI.
- **Every `assert` MUST have a message.** oxitest runs with `strict = "abort"` — bare asserts are violations. The message explains *why* the assertion matters — oxitest already shows the where, when, and what (expected vs actual). The message gives the developer the *why* so they can debug the *how*. Bad: `"expected 4 methods, got 3"` (oxitest already shows that). Good: `"FixtureProvider protocol added a method — HostProvider needs to implement it to avoid runtime TypeError"`.

### Testing guidelines

Tests in `python/tests/` must follow these rules:

1. **No class-based tests.** Use standalone `def test_*()` functions. The only exception is a class that shares `@oxi.parametrize` parameters across all its methods.
2. **Arrange, Act, Assert.** Every test should have three clear phases: set up test data (arrange), call the thing being tested (act), check the result (assert). Don't interleave setup and assertions.
3. **Dogfood oxitest features.** We are our own best user feedback. Always prefer oxitest APIs over stdlib/third-party equivalents:
   - `oxi.raises()` not `try/except` or `assertRaises`
   - `oxi.warns()` or `WarnCapture` not `warnings.catch_warnings()`
   - `TempDir` fixture not `tempfile.mkdtemp()` or `tempfile.TemporaryDirectory()`
   - `Patcher` fixture not `unittest.mock.patch` or raw `os.environ` manipulation
   - `StdCapture`/`FdCapture` not manual `sys.stdout` redirection
   - `LogCapture` not manual `logging.Handler` setup
   - `@oxi.parametrize` for multiple similar cases, not copy-pasted test functions
   - Dataclass-based test doubles not `unittest.mock.MagicMock`
   - Exception: when testing an oxitest feature itself requires bootstrapping (e.g., testing `Patcher` needs direct `os.environ` access), stdlib is acceptable in the arrange phase.
4. **Import helpers from oxitest.** Shared test utilities live in `python/tests/conftest.py` and are accessed via `from oxitest import helpers`. Use `helpers.common.<function>()` — never `sys.path.insert`.

## Agent skills

### Issue tracker

GitHub Issues on `kalonji-tools/oxitest`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default label vocabulary (needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — `CONTEXT.md` + `docs/adr/` at root. See `docs/agents/domain.md`.
