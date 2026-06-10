# Parallelism

!!! abstract "Explanation"
    Why oxitest uses subprocess workers for parallel execution, the trade-offs that come with it,
    and the planned redesign once PyO3 gains free-threaded Python support.

## Why not threads?

The obvious way to run tests in parallel is to use threads — create one thread per test, let the
OS schedule them, collect results. Every major test runner has tried some version of this, and
every one hits the same wall: CPython's Global Interpreter Lock.

The GIL is a mutex that CPython holds whenever it executes Python bytecode. Only one thread
executes Python at a time, regardless of how many CPU cores are available. Two threads that both
want to run a test function take turns rather than running simultaneously. For CPU-bound work —
which test execution is — threading provides no parallelism benefit and adds synchronization
overhead on top.

The GIL exists to protect CPython's reference-counted memory model. Removing it without breaking
the entire Python extension ecosystem is a multi-year project. For now, any Python test runner
that wants true parallelism cannot get it from threads.

## The subprocess worker model

oxitest's current parallel execution model works around the GIL by not fighting it.

Instead of threads, oxitest spawns a pool of Python subprocesses — `python -m oxitest._bridge.worker`
— each of which runs its own CPython interpreter with its own GIL. Because each process has a
separate interpreter, they can genuinely execute Python bytecode simultaneously on separate cores.
The GIL inside each worker affects only that worker; it does not block the others.

The Rust coordinator spawns worker subprocesses and distributes test groups across them.
Each worker runs its own Python interpreter, avoiding GIL contention between workers.

## The cost of this model

Subprocess workers solve the GIL problem but introduce two costs that threads would not have.

**Spawn overhead.** Starting worker processes has a fixed cost. oxitest hides this by
pre-warming workers before the first task is ready. For small suites where the spawn cost
would exceed any benefit, oxitest runs serially instead.

**Serialization overhead.** Tasks and results pass through JSON encoding between processes.
This cost is small and constant per test.

## When parallel mode is chosen

On the first run (cold cache), oxitest uses a test-count threshold (default: 100 tests).
After the first run, the timing cache holds measured durations. oxitest compares the
estimated total duration against the spawn cost for the configured worker count and picks
whichever mode is faster.

## Three-phase parallel execution

When parallel mode is chosen, oxitest partitions tests into three groups before
dispatching to workers:

1. **Inprocess tests** — tests marked with `@oxi.mark.inprocess` are held back
   and run on the main process. These tests need access to resources (e.g. a
   debugger, global state) that do not survive serialization to a worker.

2. **Auto-arranged tests** — tests that inject `shared=True` fixtures are grouped
   by fixture affinity. oxitest computes connected components of the fixture
   dependency graph and assigns each component to the main process. This prevents
   shared fixture values from being serialized across process boundaries.

   Auto-arrangement applies when the total test count is below the
   `min_parallel_tests` threshold (default: 100). The `auto_arrange_threshold`
   key (default: 70%) guards against degenerate cases: if the largest group
   exceeds this percentage of total tests, oxitest falls back to serial
   execution instead. Set `auto_arrange = false` in `pyproject.toml` to
   disable auto-arrangement entirely.

3. **Remaining tests** — everything else is distributed across worker processes
   by the scheduler.

This partitioning happens transparently. Run with `-v` (verbose) to see which
tests land in each group and why.

## Future: free-threaded Python

CPython 3.13+ includes an experimental build mode without the GIL. When PyO3's support
for free-threaded builds stabilizes, oxitest's parallel model can be redesigned to use
threads instead of subprocesses. Progress is tracked in
[issue #74](https://github.com/kalonji-tools/oxitest/issues/74).

## Parallel failure context

When a test fails during parallel execution, oxitest shows which worker ran
the test and which other tests were running concurrently. This context
appears as a single line after the failure diagnostic:

```text
FAILED  tests/test_db.py::test_write_user  42.3ms
  worker #2 | concurrent: test_read_user, test_delete_session, test_login (+5 more)
```

Workers are numbered starting from 1. Up to three concurrent test names are
shown; if more were in flight, the count is appended as `(+N more)`. When no
other tests were running, the line reads `concurrent: (none)`.

This context is only present for parallel runs — serial mode does not produce
it.

## See also

- [Run in parallel](../how-to/run-in-parallel.md) — practical guide to parallel configuration
- [Worker protocol](https://kalonji-tools.github.io/oxitest/internals/worker-protocol.html) — the JSON wire format (internals book)
- [Configuration](../reference/configuration.md) — `auto_arrange`, `spawn_overhead_ms`, `min_parallel_tests`
- [Performance](performance.md) — where speedups come from
