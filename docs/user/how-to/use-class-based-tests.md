# Use class-based tests

!!! abstract "How-to"
    Organize related tests into classes using `Test*` naming.

## Defining a test class

Any class whose name starts with `Test` is collected. Methods whose name starts
with `test_` become individual test items.

```python
--8<-- "python/tests/docs/how-to/test_class_based.py:basic-class"
```

Run: `oxitest tests/`

## Node IDs

Each method gets a node ID in the format `file.py::ClassName::method_name`:

```text
tests/test_stack.py::TestStack::test_push
tests/test_stack.py::TestStack::test_pop
```

You can filter by class name with `-E`:

```bash
oxitest tests/ -E 'name(TestStack)'
```

## Using fixtures

Fixture injection works the same as with bare functions — annotate parameters
with `Fixture[T]`:

```python
from oxitest import Fixture
from conftest import db_conn

class TestUsers:
    def test_create(self, db_conn: Fixture[Connection]):
        db_conn.execute("INSERT INTO users ...")

    def test_list(self, db_conn: Fixture[Connection]):
        assert len(db_conn.execute("SELECT * FROM users")) >= 0
```

## Using marks

Apply marks to the class (affects all methods) or to individual methods:

```python
--8<-- "python/tests/docs/how-to/test_class_based.py:class-marks"
```

## Limitations

- **No class-scoped fixtures.** Fixtures are scoped to `each` (per-test),
  `shared` (per-module), or `session` (per-process) — there is no `class` scope.
  Each test method gets its own `each`-scoped fixture instances.
- **No `setup_method` / `teardown_method`.** Use fixtures with yield teardown
  instead.
- **`self` is not injected.** The `self` parameter is the class instance, not a
  fixture. oxitest creates a new instance per test method.

## See also

- [Use fixtures](use-fixtures.md) — fixture injection, scopes, and teardown
- [Use markers](use-markers.md) — apply marks to classes or methods
- [Use parametrize](use-parametrize.md) — share parametrize cases across class methods
