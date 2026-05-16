# Marks

!!! abstract "Reference"
    Python API reference for oxitest mark decorators. Access all marks via
    ``oxitest.mark``.

Mark decorators are applied to test functions at import time. The runner reads
each function's `_oxitest_marks` list and acts on them before executing the test.

---

## mark.skip

Skip the test unconditionally.

```python
@oxitest.mark.skip(reason="not implemented yet")
def test_feature() -> None:
    ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `reason` | `str` | Human-readable explanation shown in the report. Optional. |

---

## mark.skipif

Skip the test when a condition is truthy at collection time.

```python
import sys

@oxitest.mark.skipif(sys.platform == "win32", reason="POSIX only")
def test_symlinks() -> None:
    ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `condition` | `bool` | Evaluated at import time. Test is skipped when truthy. |
| `reason` | `str` | Human-readable explanation. Keyword-only. |

---

## mark.xfail

Expect the test to fail. A failing test is reported as `XFAIL` (expected failure);
a passing test is reported as `XPASS` (unexpected pass).

```python
@oxitest.mark.xfail(reason="known regression in upstream library")
def test_broken_thing() -> None:
    assert broken_function() == 42  # currently raises
```

With `strict=True`, an `XPASS` is treated as a test failure:

```python
@oxitest.mark.xfail(reason="regression #123", strict=True)
def test_must_still_fail() -> None:
    ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reason` | `str` | — | Human-readable explanation. |
| `strict` | `bool` | `True` | If ``True``, an unexpected pass fails the suite. |
| `raises` | `type[Exception] \| None` | `None` | If set, only failures of this exception type count as `XFAIL`. Other exceptions are reported as errors. |

!!! warning "Not yet implemented"
    The `raises` parameter is planned (#102) but not yet functional. Currently
    any exception counts as XFAIL regardless of type.

---

## mark.timeout

Fail the test if it runs longer than the given number of seconds.

```python
@oxitest.mark.timeout(seconds=5)
def test_fast_path() -> None:
    result = compute()
    assert result is not None
```

The positional form is also accepted:

```python
@oxitest.mark.timeout(5)
def test_fast_path() -> None:
    ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `seconds` | `int` | Maximum allowed duration. Must be a positive integer. Validated at import time. |

!!! note
    A project-wide default timeout can be set in ``pyproject.toml`` under
    ``[tool.oxitest] timeout = N``. ``mark.timeout`` overrides it per test.

---

## mark.usefixtures

Inject one or more fixtures by name without adding them as parameters. Useful
for autouse-style side effects when the fixture value is not needed.

```python
@oxitest.mark.usefixtures("reset_db", "clear_cache")
def test_insert() -> None:
    # reset_db and clear_cache run before this test
    ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `*names` | `str` | Fixture names to inject. Each must be registered in a ``conftest.py`` ``Fixtures()`` instance visible to this test. |

---

## mark.parametrize

Alias for ``@oxitest.parametrize``. Applies named test cases to a test function.
Prefer ``@oxitest.parametrize`` directly for clarity.

```python
@oxitest.mark.parametrize(basic=AddCase(x=1, y=2, expected=3))
def test_add(x: int, y: int, expected: int) -> None:
    assert x + y == expected
```

See the [parametrize how-to](../../how-to/use-parametrize.md) for full documentation.

---
