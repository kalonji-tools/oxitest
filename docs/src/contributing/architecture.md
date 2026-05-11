# Architecture

!!! abstract "Contributing"
    A map of oxitest's internal modules and pipeline execution order.

## Pipeline overview

All test execution flows through the `run()` function in `src/lib.rs`. This function owns the
full lifecycle: discovery, collection, filtering, scheduling, execution, and cache update.

```text
collect_files          — walk the filesystem; find test files and conftest files
 FixtureSession::new    — import conftest modules; register fixtures
cache.invalidate_modules — drop cached entries for deleted modules
collect_items          — import test modules; extract TestItem list; consult cache
filter_items           — apply -k keyword filter
filter_by_marker_expr  — apply -m marker expression
filter_last_failed /   — apply --lf or --ff against cached outcomes
sort_failed_first
cache.invalidate       — prune timing cache to match final item set
group_by_module        — partition items by source file
session.register_module_count — tell fixture session the test count per module
cache.sort_groups      — sort groups by cached duration, heaviest-first
parallel vs serial     — decide based on cold/warm cache thresholds
run_phase /            — execute tests; collect TestTiming results
run_phase_parallel
cache.merge            — update timing entries for executed tests
cache.record_outcomes  — record pass/fail for --lf/--ff
cache.save             — write updated cache to disk
```

**Parallel vs serial decision:**

- *Cold cache* (no timing data): run serially if collected test count is below `min_parallel_tests` (default: 100).
- *Warm cache* (timing data available): run serially if estimated total duration ≤ `spawn_overhead_ms × worker_count`.

When running in parallel, `src/parallel.rs` spawns subprocess workers (`python -m oxitest._bridge.worker`).
Each worker receives test groups over stdin as JSON, executes them, and streams results back over stdout
as JSON. The work-stealing scheduler in `src/scheduler.rs` distributes groups and collects results —
all in Rust, without GIL contention at the coordination layer.

## Module reference

| Module | File | Responsibility |
|--------|------|----------------|
| `lib` | `src/lib.rs` | Pipeline orchestrator — ties all modules together; entry point for `run()` |
| `config` | `src/config.rs` | CLI parsing (`clap`), config loading from `pyproject.toml`, `Config` struct |
| `collector` | `src/collector.rs` | File system walk: finds test files and conftest files |
| `bridge` | `src/bridge.rs` | PyO3 bridge: imports Python modules, collects `TestItem` list, runs individual tests |
| `types` | `src/types.rs` | Core data types: `TestItem`, `TestOutcome`, `NodeId`, `CollectError`, `TestTiming` |
| `filter` | `src/filter.rs` | Keyword filtering, marker name validation, `--lf`/`--ff` logic, `group_by_module` |
| `marker` | `src/marker.rs` | Marker expression parser and evaluator (`and`/`or`/`not`) |
| `cache` | `src/cache.rs` | Timing cache: load/save `timings.json`, invalidation, duration estimation, `sort_groups` |
| `scheduler` | `src/scheduler.rs` | Work-stealing scheduler; preserves insertion order; cache pre-sorts groups by duration |
| `parallel` | `src/parallel.rs` | Subprocess worker pool: spawns `python -m oxitest._bridge.worker`, communicates over stdio JSON |
| `reporter` | `src/reporter/` | Terminal output (TTY + CI), JSON output (`--json`), progress bars, timing summaries |
