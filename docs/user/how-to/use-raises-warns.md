# Use raises, warns, and importorskip

!!! abstract "How-to"
    Assert that code raises exceptions, emits warnings, or skip tests when optional dependencies are missing.

## Assert an exception is raised

Use `oxitest.raises()` as a context manager to assert that a block of code raises
a specific exception:

```python
import oxitest

def test_divide_by_zero():
    with oxitest.raises(ZeroDivisionError):
        result = 1 / 0
```

If the block does **not** raise, the test fails with:
`AssertionError: Expected ZeroDivisionError to be raised`

### Match the exception message

```python
def test_invalid_input():
    with oxitest.raises(ValueError, match="must be positive"):
        validate_age(-1)
```

`match` is a regex pattern checked against `str(exc)` via `re.search`. oxitest fails
the test if the exception is raised but the pattern does not match the message.

### Inspect the exception

```python
def test_error_code():
    with oxitest.raises(KeyError) as exc_info:
        d = {}
        _ = d["missing"]
    assert exc_info.value.args[0] == "missing"
```

The context manager exposes `.value` — the caught exception — after the block.

### Accept multiple exception types

```python
def test_any_io_error():
    with oxitest.raises((OSError, IOError)):
        open("/nonexistent/path")
```

Pass a tuple of types to accept any of them.

## Assert a warning is emitted

Use `oxitest.warns()` to assert that a block emits a specific warning:

```python
import warnings
import oxitest

def test_deprecation_warning():
    with oxitest.warns(DeprecationWarning):
        warnings.warn("old_function is deprecated", DeprecationWarning)
```

### Match the warning message

```python
def test_warning_message():
    with oxitest.warns(UserWarning, match="disk space"):
        warnings.warn("low disk space", UserWarning)
```

`match` behaves the same as in `raises` — a regex pattern that oxitest checks against
the warning message string.

## Skip when a dependency is missing

Use `oxitest.importorskip()` to skip a test if an optional package is not
installed. Call it at the top of the test function or at module level:

```python
import oxitest

def test_with_pandas():
    pd = oxitest.importorskip("pandas")
    df = pd.DataFrame({"a": [1, 2, 3]})
    assert len(df) == 3
```

If `pandas` is not importable, the test is skipped with:
`could not import 'pandas': No module named 'pandas'`

If the module is present, `importorskip` returns it — assign the result to use
it without a second import.

### Provide a custom skip message

```python
pd = oxitest.importorskip("pandas", reason="pandas required for CSV tests")
```

`reason` overrides the default skip message.

## See also

- [Use built-in fixtures](use-builtin-fixtures.md) — `WarnCapture` for capturing all warnings in a test
- [Utilities reference](../reference/python-api/utilities.md) — API docs for `raises`, `warns`, `importorskip`
