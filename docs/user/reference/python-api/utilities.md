# Utilities

!!! abstract "Reference"
    Python API reference for oxitest utility functions.

## raises

Context manager that asserts a block raises a specific exception.

::: oxitest._bridge._raises.raises
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## warns

Context manager that asserts a block emits a specific warning.

::: oxitest._bridge._warns.warns
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## importorskip

Import a module or skip the test if the module is not installed.

::: oxitest._bridge._importorskip.importorskip
    options:
      show_root_heading: false
      show_root_toc_entry: false
      heading_level: 3

## approx

Assert that two values (or nested containers of values) are approximately equal.
Supports floats, ints, `Decimal`, and nested lists/tuples/dicts.

```python
import oxitest

def test_pi():
    assert 3.14 == oxitest.approx(3.14159, abs=0.01)

def test_vector():
    assert [0.1 + 0.2, 0.3] == oxitest.approx([0.3, 0.3])

def test_mapping():
    assert {"x": 1.0, "y": 2.0} == oxitest.approx({"x": 1.0, "y": 2.001}, abs=0.01)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `expected` | scalar, sequence, or mapping | — | The expected value(s) |
| `rel` | `float` | `1e-6` | Maximum relative tolerance |
| `abs` | `float` | `1e-12` | Maximum absolute tolerance |
| `nan_ok` | `bool` | `False` | If `True`, `NaN == NaN` is considered equal |

Comparison is symmetric and delegates to `math.isclose()` for scalars.

## skip

Skip the current test at runtime. Call inside a test body to bail out early.

```python
import oxitest

def test_only_on_linux():
    import sys
    if sys.platform != "linux":
        oxitest.skip("requires Linux")
    assert True
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reason` | `str` | `""` | Message shown in the skip report |

## See also

- [Use raises, warns, and importorskip](../../how-to/use-raises-warns.md) — how-to guide with examples
- [Marks reference](marks.md) — `@oxi.mark.skip` and `@oxi.mark.xfail` decorators
