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

## Compose parametrize layers

When a test has multiple independent dimensions (e.g. database backends × operations),
writing every combination by hand is tedious. Stack multiple `@oxitest.parametrize`
decorators with `oxitest.partial()` to express the cartesian product:

```python
from dataclasses import dataclass
import oxitest
from oxitest import Fixture, FixtureRef, partial

@dataclass
class QueryCase:
    db: FixtureRef[object]
    query: str
    expected_rows: int

@oxitest.parametrize(
    real=partial(QueryCase, db=real_db),
    mock=partial(QueryCase, db=mock_db),
)
@oxitest.parametrize(
    users=partial(QueryCase, query="SELECT * FROM users", expected_rows=3),
    empty=partial(QueryCase, query="SELECT * FROM empty", expected_rows=0),
)
def test_query(db: Fixture[object], query: str, expected_rows: int) -> None:
    rows = db.execute(query).fetchall()
    assert len(rows) == expected_rows
```

This produces 4 test variants: `test_query[real-users]`, `test_query[real-empty]`,
`test_query[mock-users]`, `test_query[mock-empty]`.

### How it works

1. Each `@oxitest.parametrize` layer provides a **subset** of the dataclass fields
   via `partial()`.
2. At collection time, oxitest takes the cartesian product of all layers and merges
   the partial fields into a complete instance.
3. The test function receives the same kwargs as a regular parametrized test — the
   signature does not change.

### Rules

- All `partial()` calls across layers must target the **same dataclass type**.
- Each layer must provide **disjoint fields** — no field may appear in two layers.
- The **union** of all layers must cover every field on the dataclass.
- Composition requires **at least 2** stacked `@parametrize` layers. A single layer
  with `partial()` values is an error — use a full dataclass instance instead.
- The source dataclass does **not** need to be frozen. Only full (non-composed)
  parametrize cases require `@dataclass(frozen=True)`.

### Compact mode

Compact mode works with composition. Annotate a single parameter with the dataclass
type to receive the merged instance:

```python
@oxitest.parametrize(real=partial(QueryCase, db=real_db))
@oxitest.parametrize(users=partial(QueryCase, query="SELECT 1", expected_rows=1))
def test_query_compact(case: QueryCase) -> None:
    rows = case.db.execute(case.query).fetchall()
    assert len(rows) == case.expected_rows
```

### Test IDs

Composed test IDs join each layer's case name with a dash, outer decorator first:

```text
test_query[real-users]
test_query[real-empty]
test_query[mock-users]
test_query[mock-empty]
```

Filter with `-k` as usual:

```console
$ oxitest -k "real"        # all real-db variants
$ oxitest -k "real-users"  # one specific combination
```

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

## See also

- [Use fixtures](use-fixtures.md) — fixture injection and `FixtureRef[T]` for parametrize cases
- [Filter tests](filter-tests.md) — run a specific parametrized variant with `-k`
- [Strict mode](../explanation/strict-mode.md) — why strict mode requires dataclasses over dicts
