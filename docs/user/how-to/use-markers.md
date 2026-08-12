# Use markers

!!! abstract "How-to"
    Tag tests with marks to skip, xfail, set timeouts, or group by category.

!!! info "Deep dive"
    See [Pipeline Deep Dive](../../../internals/book/pipeline.html) for how markers are extracted during the prescan phase without importing Python modules.

## Register custom markers

Declare custom marker names in `pyproject.toml` before use. Unregistered marks abort the
run with an error.

```toml
[tool.oxitest]
markers = [
    "slow: marks tests that take more than a second",
    "integration: hits a real database or network",
]
```

The format is `"name: description"` or just `"name"`. The description is for humans only —
oxitest strips it when validating marks.

## Apply a custom marker

```python
--8<-- "python/tests/docs/how-to/test_markers.py:custom-marker"
```

## Filter by marker

Run only tests tagged `slow`:

```console
$ oxitest -E 'mark(slow)'
```

Combine expressions with `&`, `|`, `!`:

```console
$ oxitest -E 'mark(slow) & !mark(integration)'
$ oxitest -E 'mark(slow) | mark(integration)'
$ oxitest -E '!mark(slow)'
```

Expressions are matched against the full set of marks on each test.

## Skip a test unconditionally

=== "With reason (recommended)"
    ```python
    --8<-- "python/tests/docs/how-to/test_markers.py:skip-with-reason"
    ```

=== "Without reason"
    ```python
    --8<-- "python/tests/docs/how-to/test_markers.py:skip-without-reason"
    ```

You can also skip imperatively from inside a test:

```python
--8<-- "python/tests/docs/how-to/test_markers.py:skip-imperative"
```

## Skip conditionally

```python
--8<-- "python/tests/docs/how-to/test_markers.py:skip-conditional"
```

The `when` argument is any expression evaluated at collection time. When falsy, the mark
is not applied and the test runs normally.

## Mark a test as expected to fail

```python
--8<-- "python/tests/docs/how-to/test_markers.py:xfail"
```

- If the test **fails** as expected: reported as `XFAIL` (not a failure).
- If the test **passes** unexpectedly: reported as `XPASS` (treated as a failure).

## Set a per-test timeout

```python
--8<-- "python/tests/docs/how-to/test_markers.py:timeout"
```

oxitest stops the test and reports it with status `timeout` if it exceeds the limit.
`seconds` must be a positive integer. A global timeout can also be set in
`pyproject.toml` via the `timeout` key.

The limit covers the **test function itself**. Fixture setup and fixture teardown run
outside it, so a fixture that takes longer than the limit does not make the test time
out — and a fixture that hangs is stopped by oxitest's watchdog rather than by the
timeout. This is the same for sync and async tests.

!!! warning "A blocking call is not interrupted on Windows"

    What a deadline can interrupt depends on what the test **body** is doing, and
    on the platform:

    | The test body is | Linux / macOS | Windows |
    |---|---|---|
    | running Python code, or `await`ing | bounded | bounded |
    | blocked in a C call — `time.sleep`, a socket read, `subprocess.wait` | bounded | **not bounded** |

    The table is about the body because that is what the deadline covers. Fixture
    setup and teardown are outside it on every platform.

    On Unix the deadline is delivered by `SIGALRM`, which interrupts a blocking
    call. Windows has no equivalent, so oxitest raises the timeout at the next
    Python bytecode boundary — which a blocking call does not reach until it
    returns. A test that sleeps for 5 seconds under a 1-second timeout is
    reported as a timeout on Windows, but only after the full 5 seconds.

!!! warning "A deadline can be taken away from oxitest on Unix"

    "Bounded" above assumes oxitest still holds the timer. On Unix the deadline
    is delivered by **one process-global timer**, and oxitest does not own it
    exclusively. A test that writes that timer — directly with `signal.alarm` or
    `signal.setitimer`, or through a library that does — voids its own deadline:

    ```python
    def test_writes_the_timer():
        signal.alarm(1)
        signal.alarm(0)   # the test's own deadline is gone with it
        time.sleep(30)    # no longer bounded by anything
    ```

    oxitest cannot prevent this — the timer is not oxitest's to lock. It reports
    it instead: a test whose deadline was taken is reported as **warned** rather
    than passed, because it did not run under the deadline it declared. Windows
    is unaffected: it uses a per-test timer with no shared slot.

    Nesting is different from interference and is handled. A test that runs
    another test — through oxitest's own in-process API — keeps its deadline,
    and the nested deadline is capped so it can never extend the enclosing one.
    The effective deadline is always the shortest of the live deadlines.

## Apply marks to every test in a file

Set the `oxi_mark` module-level variable to apply one or more marks to every
test function in the file:

```python
--8<-- "python/tests/docs/how-to/test_markers.py:module-mark"
```

Both tests inherit the 5-second timeout. To apply multiple marks, use a list:

```python
--8<-- "python/tests/docs/how-to/test_markers.py:module-mark-list"
```

## Force a test to run on the main process

Use `@oxi.mark.inprocess` to exclude a test from worker subprocesses during
parallel runs. The test runs on the coordinator process instead:

```python
--8<-- "python/tests/docs/how-to/test_markers.py:inprocess"
```

This is useful for tests that require `breakpoint()`, global mutable state, or
resources that cannot be serialized to a subprocess.

## See also

- [Filter tests](filter-tests.md) — filter by name with `-E 'name(...)'` or by marker with `-E 'mark(...)'`
- [Run in parallel](run-in-parallel.md) — how `@oxi.mark.inprocess` interacts with parallel execution
- [Strict mode](../explanation/strict-mode.md) — how strict mode enforces marker descriptions and other conventions
