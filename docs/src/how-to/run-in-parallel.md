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
