# Marks

!!! abstract "Reference"
    Python API reference for oxitest mark decorators. Access all marks via
    ``oxitest.mark``.

Mark decorators are applied to test functions at import time. The runner reads
each function's `_oxitest_marks` list and acts on them before executing the test.

---

## mark.skip

Skip the test unconditionally, or conditionally when `when` is truthy.

```python
--8<-- "python/tests/docs/reference/test_marks.py:skip-reference"
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `when` | `bool` | `True` | Evaluated at import time. Test is skipped when truthy. When falsy, no mark is attached. |
| `reason` | `str` | `""` | Human-readable explanation shown in the report. |

Both parameters are keyword-only. Positional arguments are rejected.

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

!!! warning "One `mark.timeout` per test"

    A second `mark.timeout` on the same test raises `ValueError` at import
    time. Two deadlines on one test is an authoring mistake: the effective
    deadline is the shorter of the two, so the other one never applies, and
    which one survives depends on decorator order.

    ```python
    @oxitest.mark.timeout(seconds=2)
    @oxitest.mark.timeout(seconds=20)   # ValueError at import
    def test_two_deadlines() -> None:
        ...
    ```

    A module-level `oxi_mark` timeout does not collide with a per-test one.
    The per-test mark wins and the module mark is not applied.

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

## mark.inprocess

Forces the test to run on the main process instead of a worker subprocess. Useful
for tests that mutate process-global state (`os.environ`, `signal` handlers,
`sys.modules`) or depend on session-scoped shared fixtures.

```python
--8<-- "python/tests/docs/reference/test_marks.py:inprocess-reference"
```

No-op when `--serial` is active (all tests already run on main process).

---

## Module-level marks (`oxi_mark`)

Apply marks to every test in a file using a module-level variable:

```python
import oxitest

oxi_mark = [oxitest.mark.slow, oxitest.mark.timeout(120)]

def test_a(): ...  # inherits slow + timeout(120)
def test_b(): ...  # inherits slow + timeout(120)
```

A single mark can be assigned directly (no list wrapper needed):

```python
oxi_mark = oxitest.mark.slow
```

### Precedence

Per-test marks override module marks of the same name:

```python
oxi_mark = [oxitest.mark.timeout(120)]

@oxitest.mark.timeout(5)
def test_fast(): ...   # timeout(5)
def test_slow(): ...   # timeout(120)
```

### Validation

- Non-mark entries in `oxi_mark` are collection errors (always, not gated by `--strict`).
- `--strict` validates module-level marks the same way it validates per-test marks (e.g., `skip` without `reason=`).
- `-E 'mark(...)'` filtering sees module-level marks.

---
