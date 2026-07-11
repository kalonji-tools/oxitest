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
--8<-- "docs/user/examples/how-to/test_markers.py:custom-marker"
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
    --8<-- "docs/user/examples/how-to/test_markers.py:skip-with-reason"
    ```

=== "Without reason"
    ```python
    --8<-- "docs/user/examples/how-to/test_markers.py:skip-without-reason"
    ```

You can also skip imperatively from inside a test:

```python
--8<-- "docs/user/examples/how-to/test_markers.py:skip-imperative"
```

## Skip conditionally

```python
--8<-- "docs/user/examples/how-to/test_markers.py:skip-conditional"
```

The `when` argument is any expression evaluated at collection time. When falsy, the mark
is not applied and the test runs normally.

## Mark a test as expected to fail

```python
--8<-- "docs/user/examples/how-to/test_markers.py:xfail"
```

- If the test **fails** as expected: reported as `XFAIL` (not a failure).
- If the test **passes** unexpectedly: reported as `XPASS` (treated as a failure).

## Set a per-test timeout

```python
--8<-- "docs/user/examples/how-to/test_markers.py:timeout"
```

oxitest kills the test and marks it failed if it exceeds the timeout. `seconds` must be
a positive integer. A global timeout can also be set in `pyproject.toml` via the
`timeout` key.

## Apply marks to every test in a file

Set the `oxi_mark` module-level variable to apply one or more marks to every
test function in the file:

```python
--8<-- "docs/user/examples/how-to/test_markers.py:module-mark"
```

Both tests inherit the 5-second timeout. To apply multiple marks, use a list:

```python
--8<-- "docs/user/examples/how-to/test_markers.py:module-mark-list"
```

## Force a test to run on the main process

Use `@oxi.mark.inprocess` to exclude a test from worker subprocesses during
parallel runs. The test runs on the coordinator process instead:

```python
--8<-- "docs/user/examples/how-to/test_markers.py:inprocess"
```

This is useful for tests that require `breakpoint()`, global mutable state, or
resources that cannot be serialized to a subprocess.

## See also

- [Filter tests](filter-tests.md) — filter by name with `-E 'name(...)'` or by marker with `-E 'mark(...)'`
- [Run in parallel](run-in-parallel.md) — how `@oxi.mark.inprocess` interacts with parallel execution
- [Strict mode](../explanation/strict-mode.md) — how strict mode enforces marker descriptions and other conventions
