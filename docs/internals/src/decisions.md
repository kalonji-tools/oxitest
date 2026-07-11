# Design Decisions

oxitest records design rationale in two ways:

1. **Spec-on-issue** — before implementing a non-trivial change, the author
   posts a spec as a comment on the GitHub issue.  The spec describes the
   problem, the proposed solution, alternatives considered, and the acceptance
   criteria.  After the PR lands, the spec comment remains as the permanent
   record of _why_ the design looks the way it does.

2. **Architecture Decision Records (ADRs)** — cross-cutting design principles
   that affect many modules get a formal ADR in `docs/adr/`.  ADRs are
   numbered sequentially and carry a status (Proposed, Accepted, Superseded).

This chapter links to the specs for the most important decisions and
summarizes the ADRs.

## Key decisions

| Decision | When | Link | Rationale |
|----------|------|------|-----------|
| Subprocess worker model | v0.1 | -- | Python's GIL prevents true thread-level parallelism. oxitest spawns `python -m oxitest._bridge.worker` subprocesses, one per core. Each worker is a persistent process that reads JSON tasks from stdin and writes JSON results to stdout. This sidesteps the GIL entirely and isolates test modules from each other. Free-threaded Python (PEP 703) is not yet adopted because the ecosystem's C extensions are not thread-safe. |
| Pipeline typestate | v0.12 | [#22](https://github.com/kalonji-tools/oxitest/issues/22) | Originally `Pipeline<S>` used phantom-type states to enforce phase ordering at compile time. **Replaced in [PR #1043](https://github.com/kalonji-tools/oxitest/pull/1043)** with a non-generic `Pipeline` struct and a runtime `PipelinePhase` enum. Transition methods now guard on the expected variant with `let ... else { unreachable!(...) }`. The change removed generic complexity while keeping the sequential call chain readable. See the [Pipeline Deep Dive](pipeline.md) chapter for current design. |
| Lazy collection | v0.14 | [milestone #24](https://github.com/kalonji-tools/oxitest/milestone/24) | Before v0.14, every test module was imported to discover its tests. With lazy collection, a Rust AST prescan (`rustpython-parser`) extracts `PrescanItem` metadata -- function names, decorators, line numbers -- without importing the module. Query filters (`-E`) run against metadata first; only modules with at least one surviving item are imported. This cuts collection time dramatically for large repos when filtering narrows the selection. Dynamic modules (e.g., those using `globals()` tricks) fall back to eager collection automatically. |
| Coverage provider protocol | v0.13 | [milestone #23](https://github.com/kalonji-tools/oxitest/milestone/23) | Coverage is implemented as a plugin protocol (`CoverageProvider`), with `CoveragePyProvider` as the built-in implementation wrapping `coverage.py`. Workers inherit `COVERAGE_PROCESS_START` so that subprocess coverage is stitched together automatically. The `--cov` and `--cov-report` CLI flags activate the provider. Making coverage a protocol means alternative providers (e.g., Rust-side instrumentation) can be swapped in without changing the runner core. |
| Doctest implementation | v0.13 | [milestone #23](https://github.com/kalonji-tools/oxitest/milestone/23) | `--doctest-modules` enables doctest collection. The Rust AST extracts docstrings from module-level, class, and function bodies (`src/doctest.rs`). A `>>>` state machine parses examples from the raw docstring text. Execution is delegated to Python's stdlib `doctest` module via `python/oxitest/_bridge/_doctest_runner.py`. Node IDs use a `<doctest>` prefix. Each doctest item receives an automatic `doctest` marker and is exempt from strict-mode checks. |
| Worker pre-warming | v0.14 | [#832](https://github.com/kalonji-tools/oxitest/issues/832), [PR #834](https://github.com/kalonji-tools/oxitest/pull/834) | After the parallel decision is made and `compute_optimal_workers` determines the worker count, all workers are spawned immediately -- before the scheduler assigns any work. Python interpreter startup (100-200 ms) overlaps with fixture arrangement and other setup. `prewarm_workers()` returns a `PoolGuard` (RAII) that kills workers on `Drop`, so no manual cleanup is needed on early-return or error paths. |
| Architecture deepening | post-v0.14 | [PR #847](https://github.com/kalonji-tools/oxitest/pull/847) | A batch of nine refactors that improved internal modularity without changing external behavior: `WorkerParams` replaces 11 positional arguments; `parallel.rs` splits into `parallel/{mod,pool,drain}.rs`; `python_ast.rs` prescan logic moves to `prescan.rs`; `config/mod.rs` merge logic moves to `config/merge.rs`; `BridgeFrame` and `FrameEntry` unify into `RawFrame`; `WorkerResult` becomes a named struct; `compute_optimal_workers` moves into the config module; `ExecutionPlan` becomes a value object in `arrange.rs`; `ParametrizeBuffer` extracts to `reporter/parametrize_buffer.rs`. |
| Pipeline deepening | post-v0.14 | [PR #857](https://github.com/kalonji-tools/oxitest/pull/857), [PR #858](https://github.com/kalonji-tools/oxitest/pull/858) | `Pipeline` now holds `PipelineShared` directly (with `Deref`/`DerefMut` access) instead of going through `SetupContext`. The monolithic `phases.rs` file was split into `phases/` (one file per pipeline state), then renamed to `transitions/` in [PR #1043](https://github.com/kalonji-tools/oxitest/pull/1043). `ExecutionHarness` trait was replaced by `ExecutionDispatch` enum in the same PR. `filter_metadata()` is rewritten with `file_matches_*` predicate functions in `filter.rs`. `execute()` uses `PoolGuard` RAII and extracted `report_violations()` / `emit_shared_fixture_warning()` helpers. |
| Prescan/filter parallelism | post-v0.14 | [PR #902](https://github.com/kalonji-tools/oxitest/pull/902) | `rayon::par_iter` parallelizes two CPU-bound pipeline phases: AST prescan (parsing Python files with `rustpython-parser`) and metadata filtering (evaluating `-E` / `--lf` predicates). Both use a two-phase pattern: parallel compute, sequential accumulate — preserving deterministic ordering. Neither phase touches the GIL. |
| Query DSL miette diagnostics | post-v0.14 | [PR #902](https://github.com/kalonji-tools/oxitest/pull/902) | `DslError` now derives `miette::Diagnostic` with `#[help]` annotations. Lexer errors (`UnterminatedString`, `UnterminatedRegex`) include byte-offset `#[label]` spans, rendered via `GraphicalReportHandler` in `collected.rs`. Parser errors stay span-free for now (adding spans requires threading `SpannedToken` through the parser — large blast radius, deferred). |
| BUILTIN_MARKERS sync | v0.14 | [PR #847](https://github.com/kalonji-tools/oxitest/pull/847) | An integration test (`python/tests/integration/test_marker_sync.py`) enforces that the Rust `BUILTIN_MARKERS` constant and the Python `_BUILTIN_HANDLER_NAMES` set contain the same entries. See the [Testing Strategy](testing.md#cross-language-sync-tests) chapter for details. |

## How to document a new decision

1. **Open an issue** (or use an existing one) describing the problem and the
   desired outcome.
2. **Post a spec comment** on the issue before writing code.  The spec should
   cover:
   - Problem statement
   - Proposed design
   - Alternatives considered and why they were rejected
   - Acceptance criteria (how you will know the change works)
3. **Implement** the change in a PR that references the issue.
4. **The spec comment is the permanent record.**  Do not delete or rewrite it
   after the PR lands -- future readers should see the original reasoning, even
   if later work refines the design.

There is no formal template.  Specs range from a few paragraphs for small
changes to multi-section documents for features like lazy collection.  The key
requirement is that the _why_ is captured before the _how_ is merged.

## Architecture Decision Records

| ADR | Title | Status |
|-----|-------|--------|
| [0001](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0001-remove-graphify.md) | Remove graphify | Accepted |
| [0002](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0002-unified-fixture-backend.md) | Unified fixture backend | Accepted |
| [0003](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0003-inspect-two-mode-navigation.md) | Inspect two-mode navigation | Accepted |
| [0004](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0004-worker-lazy-imports.md) | Worker lazy imports | Accepted |
| [0005](https://github.com/kalonji-tools/oxitest/blob/main/docs/adr/0005-immutable-by-default-interfaces.md) | Immutable-by-default interfaces | Accepted |
