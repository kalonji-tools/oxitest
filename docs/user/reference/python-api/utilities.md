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
--8<-- "python/tests/docs/reference/test_utilities.py:approx"
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
--8<-- "python/tests/docs/reference/test_utilities.py:skip-runtime"
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `reason` | `str` | `""` | Message shown in the skip report |

## See also

- [Use raises, warns, and importorskip](../../how-to/use-raises-warns.md) — how-to guide with examples
- [Marks reference](marks.md) — `@oxi.mark.skip` and `@oxi.mark.xfail` decorators
