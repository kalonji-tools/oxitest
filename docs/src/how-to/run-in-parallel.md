# Run tests in parallel

!!! abstract "How-to"
    Control how oxitest distributes tests across parallel worker processes.

## Understand default behavior

oxitest automatically decides whether to run in parallel based on the number of
collected tests and cached timing data. By default, if fewer than 100 tests are
collected and there is no warm timing cache, oxitest runs in a single process to
avoid worker spawn overhead.

You typically do not need to configure anything for parallelism to work.

## Set an explicit worker count

```console
$ oxitest --workers 4
```

This overrides the automatic CPU-count default. Useful when you want to limit
resource usage on a shared machine.

## Force serial execution

```console
$ oxitest --serial
```

Runs all tests in the current process, one at a time. Use this when:

- Tests mutate shared global state that is not safe to access concurrently.
- You are debugging and want deterministic, non-interleaved output.
- You are profiling and want to isolate test time from worker spawn overhead.

`--serial` and `--workers` are mutually exclusive.

## Tune the parallel threshold

Adjust when automatic parallelism kicks in for suites without cached timing data:

```toml
[tool.oxitest]
min_parallel_tests = 50   # go parallel with fewer tests
```

Set to `1` to always go parallel (as long as there is at least one test).

## Tune the spawn overhead estimate

The scheduler uses `spawn_overhead_ms` to estimate total worker startup cost.
If your environment starts workers significantly faster or slower than the
default 250 ms, adjust this:

```toml
[tool.oxitest]
spawn_overhead_ms = 100.0   # faster environment
spawn_overhead_ms = 500.0   # slower environment (e.g. NFS, containers)
```

The scheduler uses `spawn_overhead_ms × worker_count` as the total spawn budget
when deciding whether parallelism is worth it.

## Handle tests that cannot run in parallel

oxitest does not provide automatic test isolation for shared resources (databases,
ports, files). If your tests depend on shared external state, either:

1. Use `--serial` to run everything sequentially.
2. Design tests to use isolated resources (see [Use built-in fixtures](use-builtin-fixtures.md)
   for `TempDir` and `Patcher`).

## Understand session-scoped fixture behaviour in parallel runs

Fixtures declared with `shared=True` are intended to run once per test session.
In parallel mode, oxitest spawns each worker as a separate subprocess, and each
subprocess creates its own fixture session. A `shared=True` fixture therefore
executes once **per worker process**, not once per run.

oxitest emits a warning when it detects this situation. With the default `fmt`
log layer and `RUST_LOG=warn`:

```console
WARN oxitest::lib: shared fixture will run once per worker; session-scoped fixtures are not shared across parallel worker processes — use --serial to run them once, or remove shared=True from fixtures that can be function-scoped fixtures="my_db" fixture_count=1 workers=2
```

To resolve it, choose one of these options:

1. **Use `--serial`** to run all tests in a single process:

    ```console
    $ oxitest --serial
    ```

2. **Remove `shared=True`** from fixtures that do not need true session scope. The
   default (`shared=False`) sets up the fixture once per test, which is safe across
   workers.

Cross-process fixture sharing (e.g. via sockets or shared memory) is explicitly
out of scope for oxitest. This limitation is inherent to the subprocess worker
model — see [Parallelism](../explanation/parallelism.md) for the full rationale
and the planned redesign ([#74](https://github.com/kalonji-tools/oxitest/issues/74)).
