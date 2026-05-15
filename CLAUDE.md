# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

oxitest is a Python test runner rewritten in Rust. It exposes a Python API (fixture system, marks, builtins) implemented in `python/oxitest/_bridge/`, and a Rust core that orchestrates collection, scheduling, parallel execution, caching, and reporting. The two halves communicate via PyO3.

## Commands

The project uses a `justfile` for common tasks. Ensure required tools are on `$PATH` (via `nix develop`, or installed manually):

```bash
# Check all required tools are available
just health

# Build the Rust extension (required before running Python tests)
just build

# Run Python tests (builds extension first)
just test

# Run a single Python test file
just test python/tests/test_fixtures.py

# Run Rust unit tests
just test-rust

# Run a single Rust test
just test-rust <test_name>

# Lint Python + type-check
just lint

# Format Python + Rust
just fmt

# Clean build artifacts
just clean
```

## Architecture

### Two-layer design

**Rust layer** (`src/`): Entry point is `src/lib.rs`, which exposes a single `run(args)` PyO3 function. The Rust layer handles:
- `config.rs` — CLI parsing (clap) and `pyproject.toml` config under `[tool.oxitest]`
- `collector.rs` — file discovery based on `testpaths`/`python_files` patterns
- `cache.rs` — timing cache for parallel scheduling decisions and `--lf`/`--ff` support
- `filter.rs` — keyword (`-k`) and marker (`-m`) filtering, grouping by module
- `marker.rs` — boolean marker expression parser (logos lexer)
- `parallel.rs` — spawns worker subprocesses; each worker runs `python/oxitest/_bridge/worker.py`
- `scheduler.rs` — distributes test groups across workers
- `reporter/` — TTY, CI, and JSON (CTRF) reporters
- `strict.rs` — strict-mode violation checking (bare asserts, dict parametrize, missing mark reason)
- `bridge.rs` — PyO3 calls into the Python bridge: `collect_module`, `run_test`, `FixtureSession`

**Python bridge** (`python/oxitest/_bridge/`): Pure-Python layer that does the actual test execution. Key modules:
- `executor.py` — `run_test()`: loads module, resolves fixtures/parametrize, runs test, returns `TestResult`
- `fixtures.py` — `Fixtures`, `FixtureDef`, `FixtureRegistry`, `FixtureSession`, `FixtureAccessor`; full fixture lifecycle (scope caching, yield teardown, autouse)
- `importer.py` — `collect_module()`: imports test file, discovers `test_*` functions, returns `CollectedItem` list
- `conftest_loader.py` — loads `conftest.py` files, registers their `Fixtures()` instances, builds a `FixtureSession`
- `worker.py` — entry point for parallel worker subprocesses; reads JSON tasks from stdin, writes results to stdout
- `ast_rewriter.py` — rewrites `assert` statements into `_OxitestAssertionError` calls for enriched failure output
- `parametrize.py` — resolves `@mark.parametrize` kwargs into per-case values
- `marks.py` — mark evaluation: skip, skipif, xfail, timeout, and custom marks
- `proxy.py` / `proxy_ns.py` — `FrozenProxy` (shared fixtures) and `FixturesProxy` (namespace-aware `fx: Fixtures` injection)
- `_fixture_type.py` — `Fixture[T]`, `FixtureRef[T]`, `Yields[T]` type aliases
- `_builtins/` — built-in injectable fixtures: `TempDir`, `TempDirFactory`, `StdCapture`, `FdCapture`, `Patcher`, `LogCapture`, `TestContext`

### PyO3 data contract

`BridgeResult` in `src/bridge.rs` must stay in sync with `python/oxitest/_bridge/result.py`. `CollectedItem` fields must stay in sync with the Python `collect_module` return type. When adding fields to the Python result objects, update the Rust `#[derive(FromPyObject)]` structs too.

### Parallel execution

The Rust scheduler spawns `python -m oxitest._bridge.worker` subprocesses. Each worker receives a JSON task (module path + items + conftest paths) via stdin and writes one JSON result line per test to stdout. The worker is persistent within a run — it processes tasks until stdin is closed.

### Fixture injection protocol

Parameters annotated with `Fixture[T]` are injected; unannotated parameters are NOT (except built-in types like `TempDir`, `TestContext` which carry their own injection marker). `FixtureRef[T]` is for fixture references inside `@mark.parametrize` kwargs. `Fixtures` (bare, not `Fixture[T]`) injects a `FixturesProxy` namespace accessor.

### Configuration

`[tool.oxitest]` in `pyproject.toml` controls: `testpaths`, `python_files`, `norecursedirs`, `markers`, `timeout`, `cache_max_age`, `min_parallel_tests`, `timeout_multiplier`, `spawn_overhead_ms`, `strict`. All CLI flags override pyproject values.

### Type checking

`ty check` is the project's type checker. It runs on `python/oxitest/` via `just check` and `just lint`.
