# Use parametrize

!!! abstract "How-to"
    Run one test function against multiple named input cases.

## Choose a parametrize mode

oxitest supports three modes for parametrize cases. Dataclass mode is recommended.

=== "Dataclass mode (recommended)"
    Define a frozen dataclass for your case type, then pass keyword arguments to
    `@oxitest.parametrize`. Each kwarg is a named test case; the key becomes part of
    the test ID (e.g. `test_add[basic]`).

    ```python
    from dataclasses import dataclass
    import oxitest

    @dataclass(frozen=True)
    class AddCase:
        x: int
        y: int
        expected: int

    @oxitest.parametrize(
        basic=AddCase(x=1, y=2, expected=3),
        negative=AddCase(x=-5, y=3, expected=-2),
        zero=AddCase(x=0, y=0, expected=0),
    )
    def test_add(x: int, y: int, expected: int) -> None:
        assert x + y == expected
    ```

    oxitest infers **expanded mode** from the signature: parameters whose names match
    dataclass fields receive the field values directly.

=== "Compact mode"
    Annotate a single parameter with the dataclass type to receive the whole instance:

    ```python
    @oxitest.parametrize(
        basic=AddCase(x=1, y=2, expected=3),
        negative=AddCase(x=-5, y=3, expected=-2),
    )
    def test_add_compact(params: AddCase) -> None:
        assert params.x + params.y == params.expected
    ```

    oxitest detects compact mode when exactly one non-`Fixture[T]` parameter carries
    the case type annotation.

=== "Dict mode"
    Use plain dicts instead of a dataclass. All dicts must have the same keys, and the
    keys must match the non-fixture parameters of the test function:

    ```python
    import oxitest

    @oxitest.parametrize(
        empty={"items": [], "expected": 0},
        one={"items": [42], "expected": 42},
        many={"items": [1, 2, 3], "expected": 6},
    )
    def test_sum(items: list[int], expected: int) -> None:
        assert sum(items) == expected
    ```

## Inject fixtures into parametrize cases

A dataclass field annotated `FixtureRef[T]` tells oxitest to inject a
[fixture](use-fixtures.md) for that case. Pass a fixture function as the field value:

```python
from dataclasses import dataclass
import oxitest
from oxitest import Fixture, FixtureRef

@dataclass(frozen=True)
class QueryCase:
    db: FixtureRef[object]   # will be injected
    query: str
    expected_rows: int

@oxitest.parametrize(
    real=QueryCase(db=db_conn, query="SELECT * FROM users", expected_rows=3),
    mock=QueryCase(db=mock_db, query="SELECT * FROM users", expected_rows=0),
)
def test_query(db: Fixture[object], query: str, expected_rows: int) -> None:
    rows = db.execute(query).fetchall()
    assert len(rows) == expected_rows
```

The fixture is resolved at run time with the same scope/teardown rules as any other fixture.

## Understand test IDs

oxitest names parametrized variants using the keyword argument key:

```text
test_add[basic]
test_add[negative]
test_add[zero]
```

Use `-k` to run a specific variant:

```console
$ oxitest -k "test_add[basic]"
```
