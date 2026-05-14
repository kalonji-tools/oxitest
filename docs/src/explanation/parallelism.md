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

The Rust coordinator (`src/parallel.rs`) owns the lifecycle of these worker processes. Each worker
stays alive for the entire run. The coordinator writes JSON task descriptions to each worker's
stdin and reads JSON result lines from its stdout. The protocol is newline-delimited: one line per
message, one result line per test.

```text
Rust coordinator
│
├─ worker 0 ─── stdin ──► { module, items, conftest_paths }
│               stdout ◄─ { node_id, status, message, ... }
│
├─ worker 1 ─── stdin ──► { module, items, conftest_paths }
│               stdout ◄─ { node_id, status, message, ... }
│
└─ worker 2 ─── stdin ──► ...
```

The work-stealing scheduler (`src/scheduler.rs`) distributes test groups (one group per module)
across workers. Groups are ordered heaviest-first by cached duration so that the most expensive
modules start immediately and workers do not sit idle at the end waiting for one slow module to
finish.

## The cost of this model

Subprocess workers solve the GIL problem but introduce two costs that threads would not have.

**Spawn overhead.** Starting a Python process takes time — importing the interpreter, loading
`site-packages`, and initializing the worker module. On a typical machine this is around 250ms
per worker. For a suite with 50 fast tests, the spawn cost exceeds any parallelism benefit.
oxitest decides automatically: if the estimated total test duration (from the timing cache) is
less than the spawn overhead multiplied by the worker count, it runs serially in a single process.

**Serialization overhead.** Every task sent to a worker and every result received from one passes
through JSON encoding and decoding. For a test that runs in 1ms, the serialization round-trip is
a non-trivial fraction of the total per-test cost. This is not a problem for suites where
individual tests take tens or hundreds of milliseconds, but it shows up in benchmarks of trivially
fast tests.

Both costs are bounded and predictable. The spawn cost is paid once per worker per run. The
serialization cost per test is small and constant. The model works well in practice for suites
where tests do real work.

## The cache-driven serial/parallel decision

Because the subprocess spawn cost is fixed and known, oxitest can decide at runtime whether
parallelism will help.

On the first run (cold cache), it falls back to a test-count threshold: fewer than 100 tests runs
serially. After the first run, the cache (`src/cache.rs`) holds the measured duration of each test
from the previous run. Before spawning any workers, oxitest estimates the total suite duration by
summing cached timings. If the estimate exceeds the spawn overhead for the configured worker count,
it runs in parallel. If not, it runs serially.

This means a suite of 200 tests that each take 0.5ms (total: 100ms) will run serially even though
it exceeds the count threshold, because three workers at 250ms each would take 750ms just to start.
A suite of 20 tests where each takes 2 seconds will run in parallel even though it is small,
because the 40 seconds of test time dwarfs the spawn cost.

## The planned redesign: free-threaded Python

CPython 3.13 introduced an experimental build mode that disables the GIL entirely. In a
free-threaded Python build, multiple threads can execute Python bytecode simultaneously on
separate cores. This is the long-awaited path to true in-process parallelism for CPU-bound
Python code.

PyO3 — the crate that bridges Rust and Python in oxitest — has begun adding support for
free-threaded Python builds. When that support stabilizes, oxitest's parallel execution model
can be redesigned from scratch.

**What the redesign would look like:**

Instead of spawning subprocess workers and communicating over stdio JSON, oxitest would spawn OS
threads and invoke the Python executor directly on each thread. The test function and fixture
machinery would run in-process, on real threads, with no serialization and no per-run spawn
overhead. The Rust scheduler would dispatch work to a thread pool rather than a process pool.

The boundary between Rust and Python would shrink: there would be no worker subprocess protocol,
no JSON encoding of test tasks and results, and no stdin/stdout coordination. The `BridgeResult`
struct would be populated by a direct in-process call rather than a deserialized network message.

!!! note "Why not now"
    PyO3's free-threaded support is not yet stable enough to build on. The free-threaded CPython
    build itself is still experimental in 3.13 and 3.14 — the ecosystem of C extensions has not
    fully caught up, and the safety guarantees are still being worked out.

    The subprocess worker model is a deliberate interim design. It works correctly today, its
    costs are understood and bounded, and it will be replaced entirely once the foundation it
    depends on — stable free-threaded Python support in PyO3 — is ready.

Progress on the redesign is tracked in [issue #74](https://github.com/kalonji-tools/oxitest/issues/74).
