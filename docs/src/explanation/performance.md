# Performance Model

!!! abstract "Explanation"
    Where oxitest's speed comes from, what its current limits are, and how parallel execution extends those gains.

## Two separate costs in every test run

Running a test suite has two distinct cost categories that are worth holding separate.

The first is **runner overhead**: finding test files, parsing configuration, importing modules,
formatting output, and managing the execution lifecycle. This is work the runner does regardless
of what the tests actually test.

The second is **test execution time**: the time spent inside test functions — calling the code
under test, waiting for I/O, computing assertions. This is work the runner cannot change; it is
determined entirely by what the tests do.

oxitest is faster than pytest at runner overhead. It cannot be faster than pytest at test
execution time, because test execution happens in the same Python interpreter either way.
Understanding this distinction sets accurate expectations about where speedups appear and where
they do not.

## Fast paths: what Rust handles

**File discovery.** Finding test files means walking a directory tree and matching filenames
against patterns like `test_*.py` and `*_test.py`. oxitest replaces Python's `os.walk` with
Rust's `walkdir` crate. `walkdir` uses a tight loop with minimal allocation, making direct
`readdir` syscalls without an interpreter in the path. `globset` compiles patterns once at
startup and matches them against byte slices in nanoseconds.

**Config parsing.** Reading `pyproject.toml` and turning it into a configuration struct happens
once per run. The `toml` crate parses the file natively; `serde` deserializes the result into a
typed Rust struct without runtime type dispatch.

**Output formatting.** Progress bars (via `indicatif`) and ANSI color output (via `console`) run
in Rust on the same thread as the test runner. There is no subprocess, no IPC, and no interpreter
overhead between a test completing and its result appearing on screen.

**CLI parsing.** `clap` parses command-line arguments at native speed and validates them against a
typed struct. The Python equivalent, `argparse`, has measurable startup cost that is paid before
the first line of user-visible work begins.

## What cannot be sped up

Test execution stays in Python. This is not a limitation to be engineered around — it is correct
behavior.

Test functions import the modules they test. Those modules may use any Python library, register
hooks in `sys.modules`, or rely on C extensions. The only correct way to run them is inside
CPython. No amount of Rust optimization changes the time a test takes to run.

If the tests are slow because they hit a database, make HTTP requests, or compute something
expensive, oxitest will not make them faster. The speedup is in the scaffolding around execution,
not in execution itself.

## Import isolation

Each test module is imported using `importlib.util.spec_from_file_location` with a unique name
derived from an MD5 hash of the file path. This means each module gets a distinct entry in
`sys.modules` rather than sharing one with any other import of the same file.

The reason is correctness, not performance. If two test files import the same helper module, or
if a test file is imported more than once, Python's normal module cache would return the
already-imported object. That object may carry state from a previous test. The unique naming
sidesteps the cache entirely: every import is a fresh execution of the module.

!!! note "Performance consequence"
    Import is slightly more expensive per file — there is no cache hit to exploit — but the
    alternative is subtle test pollution that is extremely difficult to debug. The trade-off
    favors correctness.

## Parallel execution

By default oxitest runs tests in parallel using a pool of Python subprocess workers
(`python -m oxitest._bridge.worker`). Each worker communicates with the Rust coordinator over
stdio using a newline-delimited JSON protocol.

The scheduler (`src/scheduler.rs`) uses a work-stealing approach and preserves insertion order —
groups are pre-sorted by cached duration (heaviest-first) so workers naturally pick up the most
expensive modules first. The Rust side owns the progress display; workers only report results.

### Automatic serial/parallel decision

For small suites the subprocess spawn overhead outweighs the parallelism benefit. oxitest decides
automatically using a two-tier strategy:

- **Warm cache** (timing data available): if the estimated total duration is less than
  `spawn_overhead_ms × worker_count`, it runs serially — the overhead of spawning workers would
  exceed the time saved by parallelism.
- **Cold cache** (no timing data): falls back to a count threshold — if the number of collected
  tests is below `min_parallel_tests` (default: 100), it runs in a single process.

Both values are configurable in `pyproject.toml`:

```toml
[tool.oxitest]
min_parallel_tests = 50       # lower the threshold
spawn_overhead_ms  = 100.0    # adjust if workers start faster/slower
```

The command line accepts explicit overrides:

```console
$ oxitest --workers 4    # explicit worker count
$ oxitest --serial       # force single-process regardless of suite size
```

## What the numbers mean in practice

Benchmarks on real projects show that the headline numbers reflect the runner overhead gap.

On a suite with hundreds of test files, collection is faster: Rust discovers files faster than
Python, and the module import path is streamlined. On a suite where most time is spent in test
execution, the gap narrows: the part oxitest cannot change dominates the total.

The practical implication: oxitest shows the most dramatic speedup on projects with large test
suites and fast individual tests. It shows modest speedup on projects where individual tests are
expensive. The parallel model adds a second lever: CPU-bound and I/O-bound suites both benefit
from running multiple tests concurrently.
