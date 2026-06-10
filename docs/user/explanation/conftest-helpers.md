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

## What qualifies as a helper

The framework collects an attribute from a conftest module when all three conditions
are true:

1. The name does **not** start with `_`
2. The value is **callable** (function or class)
3. The value is **not** a `Fixtures` instance

Constants, private functions, and fixture registries are excluded.

## Namespace scoping

Each conftest.py in the ancestor chain contributes a sub-namespace to the `helpers`
object, keyed by its directory name. Given this layout:

```
tests/
├── conftest.py          → namespace: "tests"
├── unit/
│   ├── conftest.py      → namespace: "unit"
│   └── test_foo.py
└── integration/
    ├── conftest.py      → namespace: "integration"
    └── test_bar.py
```

`test_foo.py` sees `helpers.tests` and `helpers.unit` (its ancestors).
`test_bar.py` sees `helpers.tests` and `helpers.integration` (its ancestors).
Neither sees the other's sibling namespace. This matches how conftest fixtures
are scoped.

A namespace name can be overridden with `__helpers_namespace__ = "..."` at module
scope when the directory name is too long or unclear.

## Validation

Namespace names are validated at load time. The following are rejected with a clear,
actionable error message:

- **Python keywords** (`class`, `for`, `import`, `match`, etc.)
- **Python builtins** (`int`, `list`, `print`, `type`, etc.)
- **Duplicate names** — two conftest files in the same ancestor chain producing the
  same namespace name

This validation applies to both helper namespaces and fixture namespaces
(`Fixtures()` variable names). See the [error reference](../reference/errors.md)
for the exact messages.

## Empty namespaces

`helpers` is always present on the conftest module, even when no helpers exist. A
conftest that defines only fixtures still gets a namespace — it just has no
attributes. This means `from conftest import helpers` never fails.

A conftest with helpers but no `Fixtures` instance does not trigger the "no Fixtures
instance" warning. Only a conftest with neither fixtures nor helpers warns.

## Type checker support

`helpers` is attached to the conftest module dynamically at runtime. Static type
checkers (ty, mypy) read source files and cannot see it. Projects that type-check
their test files need a `TYPE_CHECKING` stub in their conftest — this declares the
type for static analysis with zero runtime cost. See the
[how-to guide](../how-to/share-test-helpers.md#enable-type-checker-support) for the
exact snippet.
