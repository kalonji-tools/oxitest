# Use markers

!!! abstract "How-to"
    Tag tests with marks to skip, xfail, set timeouts, or group by category.

!!! info "Deep dive"
    See [Pipeline Deep Dive](../../internals/book/pipeline.html) for how markers are extracted during the prescan phase without importing Python modules.

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
import oxitest

@oxitest.mark.slow
def test_large_sort():
    data = list(range(1_000_000, 0, -1))
    assert sorted(data) == list(range(1, 1_000_001))
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
    import oxitest

    @oxitest.mark.skip(reason="feature not yet implemented")
    def test_not_ready():
        ...
    ```

=== "Without reason"
    ```python
    import oxitest

    @oxitest.mark.skip
    def test_not_ready():
        ...
    ```

You can also skip imperatively from inside a test:

```python
import oxitest

def test_platform_specific():
    import sys
    if sys.platform != "linux":
        oxitest.skip("Linux only")
    ...
```

## Skip conditionally

```python
import sys
import oxitest

@oxitest.mark.skip(when=sys.platform == "win32", reason="POSIX only")
def test_symlinks():
    ...
```

The `when` argument is any expression evaluated at collection time. When falsy, the mark
is not applied and the test runs normally.

## Mark a test as expected to fail

```python
import oxitest

@oxitest.mark.xfail(reason="upstream bug #123")
def test_known_bug():
    assert broken_function() == 42
```

- If the test **fails** as expected: reported as `XFAIL` (not a failure).
- If the test **passes** unexpectedly: reported as `XPASS` (treated as a failure).

## Set a per-test timeout

```python
import oxitest

@oxitest.mark.timeout(seconds=5)
def test_must_finish_quickly():
    result = long_running_operation()
    assert result is not None
```

oxitest kills the test and marks it failed if it exceeds the timeout. `seconds` must be
a positive integer. A global timeout can also be set in `pyproject.toml` via the
`timeout` key.

## Apply marks to every test in a file

Set the `oxi_mark` module-level variable to apply one or more marks to every
test function in the file:

```python
import oxitest as oxi

oxi_mark = oxi.mark.timeout(5)

def test_one():
    ...

def test_two():
    ...
```

Both tests inherit the 5-second timeout. To apply multiple marks, use a list:

```python
oxi_mark = [oxi.mark.timeout(5), oxi.mark.slow]
```

## Force a test to run on the main process

Use `@oxi.mark.inprocess` to exclude a test from worker subprocesses during
parallel runs. The test runs on the coordinator process instead:

```python
import oxitest as oxi

@oxi.mark.inprocess
def test_needs_debugger():
    breakpoint()
    assert True
```

This is useful for tests that require `breakpoint()`, global mutable state, or
resources that cannot be serialized to a subprocess.

## See also

- [Filter tests](filter-tests.md) — filter by name with `-E 'name(...)'` or by marker with `-E 'mark(...)'`
- [Run in parallel](run-in-parallel.md) — how `@oxi.mark.inprocess` interacts with parallel execution
- [Strict mode](../explanation/strict-mode.md) — how strict mode enforces marker descriptions and other conventions
