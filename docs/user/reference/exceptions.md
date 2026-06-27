# Exceptions and warnings reference

This page documents the public exception and warning types exported by oxitest.
Import them directly from the `oxitest` package:

```python
from oxitest import (
    FixtureShadowWarning,
    FixtureTeardownWarning,
    SharedFixtureMutationError,
)
```

---

## `FixtureShadowWarning`

**Type**: `UserWarning`

Emitted when a fixture defined in a child conftest shadows a fixture of the same
name from a parent conftest.

oxitest uses locality-wins semantics — the most-local definition takes effect —
but it warns you when shadowing occurs so the override is never silent.

**Example**:

```
tests/
  conftest.py          # defines fixture 'db'
  integration/
    conftest.py        # also defines fixture 'db' → FixtureShadowWarning
    test_queries.py
```

Capture this warning in tests with `WarnCapture` if you need to assert that
shadowing does or does not occur:

```python
import oxitest

def test_no_shadow(warn: oxitest.WarnCapture) -> None:
    # ... trigger fixture registration ...
    assert not any(
        isinstance(w.message, oxitest.FixtureShadowWarning) for w in warn.list
    ), "unexpected fixture shadow"
```

---

## `FixtureTeardownWarning`

**Type**: `UserWarning`

Emitted when an exception is raised inside the teardown phase of a yield
fixture. The exception is caught, converted to a warning, and test execution
continues — preventing a teardown failure from masking the original test result.

The warning message includes the fixture name and the test node ID where the
failure occurred.

**Example**:

```python
import oxitest

fx = oxitest.Fixtures()

@fx.fixture
def leaky_connection() -> oxitest.Yields[object]:
    conn = connect()
    yield conn
    conn.close()   # if this raises, FixtureTeardownWarning is emitted
```

Capture this warning to assert clean teardown:

```python
def test_connection_cleans_up(
    leaky_connection: oxitest.Fixture[object],
    warn: oxitest.WarnCapture,
) -> None:
    # exercise leaky_connection ...
    assert not any(
        isinstance(w.message, oxitest.FixtureTeardownWarning) for w in warn.list
    ), "fixture teardown raised an exception"
```

---

## `SharedFixtureMutationError`

**Type**: `RuntimeError`

Raised when code attempts to mutate a shared fixture value. Shared fixtures
(declared with `shared=True`) are wrapped in a `FrozenProxy` that intercepts
attribute assignment and item assignment, raising this error to prevent
cross-test contamination.

**Example**:

```python
import oxitest

fx = oxitest.Fixtures()

@fx.fixture(shared=True)
def config() -> dict[str, str]:
    return {"env": "test"}

def test_mutation(config: oxitest.Fixture[dict[str, str]]) -> None:
    config["env"] = "prod"  # raises SharedFixtureMutationError
```

Use `with oxitest.raises(oxitest.SharedFixtureMutationError):` to assert that
mutation is correctly rejected in your own plugin or fixture code.
