# Benchmarks

!!! abstract "Explanation"
    Quantitative comparison of oxitest vs pytest across suite sizes, execution modes, and cache states.

## Methodology

All benchmarks use a generated suite of stdlib-only tests (dict operations, list manipulation,
plain assertions). This isolates **runner overhead** from application-specific cost — the tests
themselves are trivially fast, so the numbers reflect what each runner adds on top.

Each tier contains a realistic mix of test types:

- **Trivial** (~50%): no fixtures, inline setup
- **Fixture** (~30%): yield-based setup/teardown
- **Parametrize** (~20%): 3–5 named cases per function

Measurements use [hyperfine](https://github.com/sharkdp/hyperfine) with 3 warmup runs and 10
timed runs. Numbers are mean wall-clock time.

??? note "Reproducing these numbers"
    ```console
    $ just bench          # run all tiers
    $ just bench-compare  # print summary table
    ```

    The bench suite generates test files at runtime into `bench/generated/` (gitignored).
    Results depend on hardware — expect different absolute numbers but similar ratios.

## Startup

A single no-op test (`def test_noop(): pass`) measures pure startup cost: interpreter launch,
runner import, configuration parsing, and minimal collection.

| Runner | Time |
|--------|------|
| oxitest | 60 ms |
| pytest  | 160 ms |
| **Speedup** | **2.7x** |

oxitest's startup advantage comes from Rust-native CLI parsing, config loading, and file
discovery. See [Performance](performance.md#fast-paths-what-rust-handles) for details.

## Serial execution

These suites run single-process (`oxitest --serial`). The "below threshold" tier is too small
for parallel mode to activate.

| Tier | Tests | oxitest | pytest | Speedup |
|------|------:|--------:|-------:|--------:|
| below threshold | 36 | 76 ms | 183 ms | **2.4x** |
| small | 216 | 155 ms | 265 ms | **1.7x** |
| medium | 492 | 375 ms | 526 ms | **1.4x** |
| large | 996 | 707 ms | 925 ms | **1.3x** |

The speedup narrows as suite size grows. This is expected: runner overhead is a fixed cost
paid once per run. As test execution time (which is identical in both runners) dominates the
total, the overhead gap becomes a smaller fraction.

At 36 tests the overhead is the majority of wall-clock time, so oxitest's advantage is most
visible. At 1000 tests, execution dominates and the gap narrows — but oxitest is still
measurably faster.

## Parallel execution

For the small, medium, and large tiers, oxitest also runs in parallel mode (automatic worker
pool). Parallel is compared against serial at the same suite size.

| Tier | Tests | Serial | Parallel | Parallel gain |
|------|------:|-------:|---------:|--------------:|
| small | 216 | 155 ms | 150 ms | 1.03x |
| medium | 492 | 375 ms | 372 ms | 1.01x |
| large | 996 | 707 ms | 695 ms | 1.02x |

The parallel gain is minimal here because these tests are trivially fast — each test completes
in microseconds. The subprocess spawn overhead roughly cancels the parallelism benefit.

Parallel mode shows its value on real-world suites where individual tests take milliseconds or
more (I/O, database access, network calls). In those cases the worker pool keeps all cores busy
while slow tests are in flight.

## Cache impact

oxitest maintains a timing cache (`.oxitest_cache/`) that records how long each test took.
On subsequent runs, the scheduler uses this data to sort modules heaviest-first and decide
whether parallel execution is worthwhile.

"Cold" means the cache was deleted before the run. "Warm" means the cache existed from a
prior run.

| Tier | Tests | Warm | Cold | Overhead |
|------|------:|-----:|-----:|---------:|
| small | 216 | 150 ms | 226 ms | +51% |
| medium | 492 | 372 ms | 421 ms | +13% |
| large | 996 | 695 ms | 891 ms | +28% |

The cold-cache penalty comes from two sources:

1. **No duration data** — the scheduler falls back to the count-based heuristic
   (`min_parallel_tests`) instead of the smarter duration-based decision
2. **No ordering data** — modules run in filesystem order rather than heaviest-first,
   leading to suboptimal work distribution across workers

The first run of any new project pays this cost. Subsequent runs benefit from cached timing
data automatically.

## What the numbers mean

oxitest is consistently faster than pytest across all suite sizes. The advantage is largest
for small suites (2–3x) where runner overhead dominates, and smallest for large suites
(1.3x) where test execution time dominates.

These benchmarks measure runner overhead in isolation using trivially fast tests. In practice,
the speedup you see depends on the ratio of runner overhead to test execution time in your
specific project:

- **Fast tests** (unit tests, pure functions): expect speedups closer to the numbers above
- **Slow tests** (database, network, filesystem): expect speedups closer to 1x — the runner
  overhead is dwarfed by I/O wait time
- **Mixed suites** with parallel mode: expect better results than shown here, because
  parallel gains scale with per-test duration
