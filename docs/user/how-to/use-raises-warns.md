# Use raises, warns, and importorskip

!!! abstract "How-to"
    Assert that code raises exceptions, emits warnings, or skip tests when optional dependencies are missing.

## Assert an exception is raised

Use `oxitest.raises()` as a context manager to assert that a block of code raises
a specific exception:

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:raises-basic"
```

If the block does **not** raise, the test fails with:
`AssertionError: Expected ZeroDivisionError to be raised`

### Match the exception message

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:raises-match"
```

`match` is a regex pattern checked against `str(exc)` via `re.search`. oxitest fails
the test if the exception is raised but the pattern does not match the message.

### Inspect the exception

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:raises-excinfo"
```

The context manager exposes `.value` — the caught exception — after the block.

### Accept multiple exception types

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:raises-tuple"
```

Pass a tuple of types to accept any of them.

## Assert a warning is emitted

Use `oxitest.warns()` to assert that a block emits a specific warning:

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:warns-basic"
```

### Match the warning message

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:warns-match"
```

`match` behaves the same as in `raises` — a regex pattern that oxitest checks against
the warning message string.

## Skip a test at runtime

Use `oxitest.skip()` to abandon a test once it is already running — when a
precondition can only be checked after setup has happened:

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:skip-runtime"
```

**`skip()` does not return.** It raises immediately, so nothing after it in the
test body executes — the assertion above never runs when no key is configured.
There is no need to guard the rest of the test with an `else`.

Your type checker knows that. `load_api_key()` returns `str | None`, and after
the `skip()` the value is a plain `str` — so `.startswith()` needs no cast and
no `assert is not None`.

Prefer `@mark.skip` when the condition is known before the test runs:

| | Decided | Setup runs first? |
|---|---|---|
| `@mark.skip(reason=...)` | at collection | no — the test never starts |
| `oxitest.skip(reason)` | during the test | **yes** — fixtures have already been built |

That difference is the reason the runtime form exists. Reach for the decorator
when you can, because skipping before setup is cheaper and reports earlier.

## Skip when a dependency is missing

Use `oxitest.importorskip()` to skip a test if an optional package is not
installed. Call it at the top of the test function or at module level:

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:importorskip-basic"
```

If `pandas` is not importable, the test is skipped with:
`could not import 'pandas': No module named 'pandas'`

If the module is present, `importorskip` returns it — assign the result to use
it without a second import.

### Provide a custom skip message

```python
--8<-- "python/tests/docs/how-to/test_raises_warns.py:importorskip-reason"
```

`reason` overrides the default skip message.

## See also

- [Use built-in fixtures](use-builtin-fixtures.md) — `WarnCapture` for capturing all warnings in a test
- [Utilities reference](../reference/python-api/utilities.md) — API docs for `raises`, `warns`, `skip`, `importorskip`
