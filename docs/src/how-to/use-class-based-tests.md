# Use class-based tests

!!! abstract "How-to"
    Organize related tests into classes using `Test*` naming.

## Defining a test class

Any class whose name starts with `Test` is collected. Methods whose name starts
with `test_` become individual test items.

```python
class TestStack:
    def test_push(self):
        stack = []
        stack.append(1)
        assert stack == [1]

    def test_pop(self):
        stack = [1, 2]
        assert stack.pop() == 2
        assert stack == [1]
```

Run: `oxitest tests/`

## Node IDs

Each method gets a node ID in the format `file.py::ClassName::method_name`:

```text
tests/test_stack.py::TestStack::test_push
tests/test_stack.py::TestStack::test_pop
```

You can filter by class name with `-k`:

```bash
oxitest tests/ -k TestStack
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
import oxitest

@oxitest.mark.slow
class TestExpensive:
    def test_heavy_computation(self):
        ...

    @oxitest.mark.skip(reason="not yet implemented")
    def test_future_feature(self):
        ...
```

## Limitations

- **No class-scoped fixtures.** Fixtures are scoped to `function` or `session`
  — there is no `class` scope. Each test method gets its own fixture instances.
- **No `setup_method` / `teardown_method`.** Use fixtures with yield teardown
  instead.
- **`self` is not injected.** The `self` parameter is the class instance, not a
  fixture. oxitest creates a new instance per test method.
