# Use the test cache

!!! abstract "How-to"
    Re-run failed tests first, skip passing tests, and inspect slow tests using oxitest's result cache.

## Understand how the cache works

oxitest writes timing and result data to `.oxitest_cache/timings.json` in the
rootdir after every run. The `--failed` flag and the scheduler both read this file
to make smarter decisions. The cache is human-readable JSON.

!!! tip "Add to .gitignore"
    Add `.oxitest_cache/` to your `.gitignore` — it is local to each developer's machine.

## Run only tests that failed last time

```console
$ oxitest --failed=only
```

Collects all tests but only runs those that were recorded as failed on the previous
run. Passing tests are skipped. If there are no previous failures recorded, all
tests run.

## Run failed tests first

```console
$ oxitest --failed=first
```

Runs all tests, but failed tests from the previous run are executed first. This
gives faster feedback during a fix-iterate cycle without skipping any tests.

## Show the slowest tests and fixtures

```console
$ oxitest --durations 10
```

Prints the 10 slowest tests **and** the 10 slowest fixtures at the end of the
run, sorted by descending duration. Useful for finding bottlenecks — especially
when a shared fixture does expensive I/O that would otherwise be invisible in the
per-test timings.

Example output:

```
slowest 3 tests
    520.15ms  tests/test_store.py::test_create
     42.30ms  tests/test_store.py::test_read
     10.00ms  tests/test_basic.py::test_add

slowest 2 fixtures
    480.00ms  persistent_store — setup 480.00ms (1)
      3.50ms  tmp_dir — setup 2.50ms (15) + teardown 1.00ms (15)
```

Each fixture entry shows total time, setup/teardown breakdown, and invocation
count. Fixtures with no teardown (non-yield) omit the teardown portion.

```console
$ oxitest --durations 0
```

`0` disables the durations report (default when flag is omitted).

!!! note
    Fixture timings are collected from the main process only. In parallel mode,
    fixtures resolved inside worker subprocesses are not included.

## Configure cache eviction

A test entry's age increments each run oxitest skips it, and resets to 0 when
oxitest runs it. Entries whose age exceeds `cache_max_age` are evicted. The
default is 50:

```toml
[tool.oxitest]
cache_max_age = 100   # keep entries for longer
```

## Find the cache location

```text
<rootdir>/
  .oxitest_cache/
    timings.json
```

The cache degrades gracefully: oxitest silently ignores a missing or corrupt cache
file and writes a fresh cache at the end of the run.

## See also

- [Use retries](use-retries.md) — retry failed tests and detect flaky tests
- [Run in parallel](run-in-parallel.md) — how the cache drives the serial/parallel decision
- [Configuration reference](../reference/configuration.md) — `cache_max_age` and other keys
