# Exceptions and diagnostics reference

This page documents the public exception types and diagnostic messages produced
by oxitest.

```python
from oxitest import SharedFixtureMutationError
```

---

## Diagnostics

oxitest emits **diagnostics** for conditions that are worth reporting but do not
fail the test. Diagnostics appear in the summary block at the end of the run,
grouped by severity (error → warning → notice) and deduplicated.

### Fixture teardown failure

**Severity**: warning
**Context**: `fixture teardown`

Emitted when an exception is raised during the cleanup phase of a yield
fixture. The exception is caught, the diagnostic is recorded, and test
execution continues — preventing a teardown failure from masking the
original test result. The message includes the fixture name and the test
node ID where the failure occurred.

### Fixture shadow

**Severity**: notice
**Context**: `fixture registration`

Emitted when two declarations of one fixture name are both reachable from some
test, so the nearer one takes effect there. oxitest resolves to the nearest
visible declaration and notifies you so the override is never silent.

```
tests/
  conftest.py                  # defines fixture 'db'
  api/
    __fixtures__.py            # also defines 'db' → notice naming tests/api
    test_queries.py            #   resolves the api declaration
  admin/
    test_reports.py            #   still resolves the conftest declaration
```

No notice is emitted for declarations that no single test can reach together —
two sibling packages, or two test modules each declaring the name inline.

---

## `SharedFixtureMutationError`

**Type**: `RuntimeError`

Raised when code attempts to mutate a shared fixture value. Shared fixtures
(declared above `function` lifetime) are wrapped in a `FrozenProxy` that intercepts
attribute assignment and item assignment, raising this error to prevent
cross-test contamination.

**Example**:

```python
--8<-- "python/tests/docs/reference/test_exceptions.py:shared-mutation-error"
```

Use `with oxitest.raises(oxitest.SharedFixtureMutationError):` to assert that
mutation is correctly rejected in your own plugin or fixture code.

---

## `FixtureTypeNotFoundError`

**Inheritance**: `FixtureTypeNotFoundError` → `FixtureNotFoundError` → `FixtureError` → `OxitestError`

Raised during by-type fixture resolution when no fixture is registered for
the requested type. The message names the three legal registration routes
so the fix is discoverable from the traceback alone:

1. A **BuiltinFixture** matching the requested type (see [Built-in fixtures](python-api/builtins.md))
2. A **plugin-provided `FixtureProvider`** whose `fixture_type` matches (see [Write plugins](../how-to/write-plugins.md))
3. A **conftest fixture** with the requested type as its return annotation

Typically fires when a test parameter is annotated `Fixture[T]` for a type
`T` that no source provides — check the fixture registration site and the
type spelling.

**Example message**:

```
no fixture registered for type 'DatabaseHandle' — must be a
BuiltinFixture, a plugin-provided FixtureProvider with matching
fixture_type, or a conftest fixture with 'DatabaseHandle' as its
return annotation.
```
