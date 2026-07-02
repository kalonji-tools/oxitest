# Conftest helpers

!!! abstract "Explanation"
    Why helpers live in conftest.py, how the namespace system works, and what
    the framework does behind the scenes.

For step-by-step usage, see the [Share test helpers](../how-to/share-test-helpers.md)
how-to guide.

## The problem

Test suites need shared utility functions — factory functions, test doubles, subprocess
wrappers. The usual approaches all have drawbacks:

- **`sys.path.insert` hacks** work but scatter path manipulation across every test file.
- **`PYTHONPATH` additions** put generic names like `helpers` on the global import path,
  risking collisions with source code.
- **Package-qualified imports** (`from tests.helpers import ...`) are layout-dependent and
  don't generalize across projects.

What you actually want is the same thing `conftest.py` already provides for fixtures:
scoped, discoverable infrastructure that test files can access without import gymnastics.

## Why conftest

`conftest.py` already has the machinery: directory-level discovery, root-first ordering,
and `sys.modules` registration. Introducing a second reserved filename would duplicate
all of that. Keeping fixtures and helpers in one file means one place to look when
debugging test infrastructure for a directory.

## How helpers are registered

Helpers use explicit registration via a `Helpers()` instance, mirroring how fixtures
use `Fixtures()`:

```python
# conftest.py
from oxitest import Helpers

utils = Helpers()

@utils.helper
def make_user(**overrides):
    defaults = {"name": "test", "email": "test@example.com"}
    return {**defaults, **overrides}
```

Only functions decorated with `@helpers.helper` are registered. Other public functions
in conftest.py are ignored — this is the opposite of the old implicit collection
system and gives authors explicit control over what is exposed.

## Namespace scoping

Each `Helpers()` instance in the ancestor conftest chain contributes a sub-namespace
to the `helpers` proxy, keyed by the variable name. Given this layout:

```
tests/
├── conftest.py          → common = Helpers()  → namespace: "common"
├── unit/
│   ├── conftest.py      → unit = Helpers()    → namespace: "unit"
│   └── test_foo.py
└── integration/
    ├── conftest.py      → integ = Helpers()   → namespace: "integ"
    └── test_bar.py
```

`test_foo.py` sees `helpers.common` and `helpers.unit` (its ancestors).
`test_bar.py` sees `helpers.common` and `helpers.integ` (its ancestors).
Neither sees the other's sibling namespace. This matches how conftest fixtures
are scoped.

## Validation

Namespace names are validated at load time. The following are rejected with a clear,
actionable error message:

- **Python keywords** (`class`, `for`, `import`, `match`, etc.)
- **Python builtins** (`int`, `list`, `print`, `type`, etc.)

This validation applies to both helper namespaces and fixture namespaces
(`Fixtures()` variable names). See the [error reference](../reference/errors.md)
for the exact messages.

## Plugin-provided helpers

Plugins can provide helpers via the `HelperProvider` protocol. Each provider contributes
a single named callable. The namespace is derived from the plugin's module name
(`provider.__module__`).

## Accessing helpers

Helpers are accessed via a read-only proxy importable from oxitest:

```python
from oxitest import helpers

helpers.common.make_user(name="alice")
```

The proxy resolves at attribute-access time via a contextvar set during session init.
Accessing `helpers` outside a test session raises `AttributeError`.

## See also

- [Share test helpers](../how-to/share-test-helpers.md) — step-by-step how-to
- [Error reference](../reference/errors.md) — namespace validation error messages
