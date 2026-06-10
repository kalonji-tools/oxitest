# Use retries and flaky test detection

!!! abstract "How-to"
    Automatically re-run failed tests and identify flaky tests — those that fail
    intermittently but pass on retry — without failing the build.

## Enable retries

```console
$ oxitest --retries 3
```

After the initial test run finishes, any test that failed is re-run serially up
to `N` times. A test that exhausts all retries remains a hard failure. A test
that passes on any retry is reported as **FLAKY** and does not count toward the
failure total.

## Add a delay between retries

Retry delay is a configuration-only option — it cannot be set on the command
line. Set it in `pyproject.toml`:

```toml
[tool.oxitest]
retries_delay = 2   # seconds to wait before each retry attempt
```

Waits 2 seconds before each retry attempt. Useful for tests that fail due to
resource contention or external service rate limits.

## Configure in pyproject.toml

Both options are available under `[tool.oxitest]` so you do not need to pass
`--retries` on every invocation:

```toml
[tool.oxitest]
retries = 3
retries_delay = 2   # seconds; config-only, not available as a CLI flag
```

`--retries` on the CLI overrides the `retries` value from `pyproject.toml`.
`retries_delay` is config-only and cannot be overridden from the command line.

## How the retry phase works

Retries run as a separate phase after the full initial execution (serial or
parallel) completes. oxitest identifies every test whose recorded outcome was a
failure (`failed`, `error`, or `timeout`) and re-runs each one serially, up to
`retries` attempts, in the order they were originally collected.

- If a test **passes** on any attempt, oxitest emits a `FLAKY` result for it and
  moves on.
- If a test **fails every attempt**, the original failure result stands and no
  additional output is emitted for it.

Retries are skipped entirely if the run was interrupted (e.g. by `--maxfail` or
`Ctrl-C`).

## Flaky test detection

A test is flaky when it fails in the initial phase but passes on at least one
retry. oxitest synthesizes a `FLAKY` outcome for it — this outcome is distinct
from `passed` and is never produced by the test worker directly.

In verbose mode the label `FLAKY` appears next to the test name and the retry
index is included in the message:

```
tests/test_network.py::test_connection   FLAKY  passed on retry 1 of 3
```

In dot-progress mode the character `f` is used instead of `.` or `F`.

## Exit codes and flaky tests

A `FLAKY` outcome is **not a hard failure**. It does not increment the failure
counter and does not cause a non-zero exit code on its own. The run exits 0 if
the only non-passing outcomes are flaky.

If some tests are both flaky and remain failed after retries, the failed tests
drive the exit code as normal.

## Summary line

Flaky tests appear in the summary line alongside other outcome counts:

```
════════════════════════════════════════════════════════════════════════════════
  15 passed · 2 warnings · 1 flaky
════════════════════════════════════════════════════════════════════════════════
```

## Interaction with the cache

When a test is detected as flaky, its cache entry is updated with:

- `last_outcome = "flaky"` — `--failed=only` and `--failed=first` do **not**
  treat flaky tests as failures, so they will not be selected by those flags on
  the next run.
- `flaky_count` — a running total of how many times the test has been flaky
  across runs, visible in `.oxitest_cache/timings.json`.

The timing recorded in the cache for a flaky test is the duration of the
successful retry attempt, not the initial failure.

## See also

- [Use the test cache](use-test-cache.md) — `--failed=only`, `--failed=first`,
  and cache eviction
- [Run in parallel](run-in-parallel.md) — retries always run serially regardless
  of the parallel settings used for the initial phase
