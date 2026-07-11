# Troubleshooting

!!! abstract "How-to"
    Diagnose and fix common problems when running tests with oxitest.

## Why are my tests running serially?

oxitest decides at runtime whether to use parallel workers. On a **cold cache**
(no `.oxitest_cache/` yet), it falls back to a test-count threshold:
parallelism only kicks in when at least `min_parallel_tests` tests are collected
(default **100**). On a **warm cache**, it compares the estimated total duration
against the spawn overhead (`spawn_overhead_ms * worker_count`).

If your suite has fewer than 100 tests and no cached timing data, oxitest
runs everything in-process to avoid the cost of spawning workers.

**Options:**

- Lower the threshold in `pyproject.toml`:

    ```toml
    [tool.oxitest]
    min_parallel_tests = 10
    ```

- Force a specific worker count:

    ```console
    $ oxitest --workers 4
    ```

- Force serial explicitly with `--serial` if that is what you want.

## Why does my test hang or timeout?

!!! warning "No default timeout"
    By default oxitest does **not** enforce a timeout — tests run until they
    finish. A single hanging test blocks the entire worker indefinitely.

By default oxitest does **not** enforce a timeout — tests run until they
finish. If a test hangs, it blocks the entire worker.

Set a per-test timeout with the CLI flag or in `pyproject.toml`:

```console
$ oxitest --timeout 30
```

```toml
[tool.oxitest]
timeout = 30
```

!!! tip "Derive timeouts from history"
    For suites with cached timing data, `timeout_multiplier` derives a per-test
    timeout from the cached duration automatically — no need to guess a fixed value.

For suites with cached timing data, `timeout_multiplier` derives a per-test
timeout from the cached duration (e.g. 3x the historical average), clamped to
at least the global timeout:

```toml
[tool.oxitest]
timeout = 10
timeout_multiplier = 3.0
```

## How do I debug a failing test?

Use these flags to narrow down and inspect failures:

```console
# Full tracebacks including call-chain frames
$ oxitest --show-internals

# One-line summary per failure
$ oxitest --tb=line

# Run a single test by keyword
$ oxitest -E 'name(test_my_function)'

# Run a single file
$ oxitest tests/test_foo.py

# Verbose output — shows each test name and result
$ oxitest -vv
```

Combine flags to isolate a specific failure: `oxitest tests/test_foo.py -E 'name(my_test)' --show-internals -vv`.

When a test fails in **parallel mode**, the failure output includes a context
line showing which worker ran the test and what other tests were running at
the same time:

```text
FAILED  tests/test_db.py::test_write_user  42.3ms
  worker #2 | concurrent: test_read_user, test_delete_session
```

This helps diagnose shared-state bugs where two tests interfere with each
other (e.g. writing to the same database row or file).

See [Debug tests](debug-tests.md) for the full `oxitest debug` workflow.

## Why is test collection slow?

!!! tip "Narrow down collection paths"
    Setting `testpaths` and `norecursedirs` is the fastest way to cut collection
    time — oxitest skips entire directory trees it never needs to scan.

oxitest walks directories recursively during collection. If it scans large
non-test trees, collection time increases.

Check these settings in `pyproject.toml`:

```toml
[tool.oxitest]
# Limit which directories are scanned
testpaths = ["tests"]

# Narrow the file-name pattern (default: test_*.py and *_test.py)
python_files = ["test_*.py"]

# Exclude directories from recursive scanning
# (defaults already exclude .git, __pycache__, .venv, venv,
#  .tox, dist, build, node_modules)
norecursedirs = [".git", "__pycache__", ".venv", "node_modules", "data"]
```

If collection is still slow, verify you are not scanning into virtual
environments, `node_modules`, or large data directories.

## How do I diagnose per-file collection time?

!!! tip "Profile before optimising"
    Run `--collection-profile` first to identify which files are slow before
    restructuring imports or splitting modules.

Use `--collection-profile` to see a timing breakdown for each file:

```console
$ oxitest --collection-profile
```

This prints prescan time (Rust-side AST analysis) and collection time (Python
import + discovery) for each test file to stderr. Look for files where collection
time is disproportionately high — they likely have expensive module-level imports.

## How do I clear the cache?

Delete the cache directory manually:

```console
$ rm -rf .oxitest_cache/
```

The cache stores timing data and last-failed outcomes in
`.oxitest_cache/timings.json`. Removing it forces a cold start — oxitest
rebuilds the cache on the next run.

Cache entries auto-expire when their age exceeds `cache_max_age` (default
**50** runs). Adjust it in `pyproject.toml`:

```toml
[tool.oxitest]
cache_max_age = 20
```

A missing or corrupt cache file is silently ignored — oxitest always writes a
fresh cache at the end of a run.

## Why does `--failed=only` show no tests?

!!! note
    `--failed=only` requires a warm cache from a previous run. On a fresh checkout
    or after `rm -rf .oxitest_cache/`, there are no recorded failures to replay.

`--failed=only` re-runs only tests that **failed, errored, or timed out** on
the previous run. If no cache exists or every test passed last time, there are
no recorded failures and all tests run normally (oxitest prints a message:
`no recorded failures — running all N tests`).

If you want to run failed tests first but still execute the full suite, use
`--failed=first` instead:

```console
# Re-run only failures (skips passing tests)
$ oxitest --failed=only

# Run failures first, then everything else
$ oxitest --failed=first
```

## See also

- [CLI reference](../reference/cli.md) — full list of command-line options
- [Error reference](../reference/errors.md) — catalog of error messages with causes and fixes
- [Exit codes](../reference/exit-codes.md) — what each exit code means

## How do I include environment info in a bug report?

Run `oxitest env` to print version, Python interpreter, Rust compiler, and
OS information:

```console
$ oxitest env
```

Include this output when filing bug reports or asking for help.

## Strict mode flags my TempDir fixture as unused

The unused-fixture check does not detect `Fixture[TempDir]` as a usage. Use
`Fixture[Path]` instead — both inject the same temporary directory:

```python
--8<-- "docs/user/examples/how-to/test_troubleshooting.py:tempdir-path-workaround"
```
