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

## Show the slowest tests

```console
$ oxitest --durations 10
```

Prints the 10 slowest tests at the end of the run, sorted by descending duration.
Useful for finding bottlenecks.

```console
$ oxitest --durations 0
```

`0` disables the durations report (default when flag is omitted).

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
