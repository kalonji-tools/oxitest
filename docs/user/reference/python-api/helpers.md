# Helpers

!!! abstract "Reference"
    API reference for the `Helpers` registry class.

## Helpers class

```python
from oxitest import Helpers
```

`Helpers()` creates an instance-based helper registry. Register callables
(functions or classes) with the `@helpers.helper` decorator. The variable
name becomes the namespace.

### Constructor

```python
Helpers(name: str | None = None)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str \| None` | `None` | Explicit namespace name. When `None`, the variable name is used (e.g., `utils = Helpers()` sets namespace to `"utils"`). |

### Decorator

```python
@helpers.helper
def my_func() -> str: ...

@helpers.helper(name="custom_name")
def my_func() -> str: ...
```

Registers a callable (function or class) with this `Helpers` instance. The
callable's `__name__` is used as the helper name unless overridden with the
`name` keyword argument.

### Access pattern

```python
from oxitest import helpers

helpers.<namespace>.<helper_name>()
```

## Plugin-provided helpers

Plugins can contribute helpers via the `HelperProvider` protocol. See
[Write plugins](../../how-to/write-plugins.md) for details.

## See also

- [Share test helpers](../../how-to/share-test-helpers.md) — how-to guide with examples
- [Conftest helpers](../../explanation/conftest-helpers.md) — design rationale
- [Fixture types](fixture-types.md) — the parallel `Fixtures` registry
