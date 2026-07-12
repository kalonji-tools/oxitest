"""Tested examples for the use-parametrize how-to guide."""

from dataclasses import dataclass

import oxitest
from oxitest import Fixture, FixtureRef, partial


# FixtureRef targets — these names must match fixtures registered in conftest.py.
# The functions exist solely as references for the parametrize decorator.
def db_conn(): ...
def mock_db(): ...
def real_db(): ...


# fmt: off
# --8<-- [start:dataclass-expanded]
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
    assert x + y == expected, f"{x} + {y} should equal {expected}"
# --8<-- [end:dataclass-expanded]

# --8<-- [start:compact-mode]
@oxitest.parametrize(
    basic=AddCase(x=1, y=2, expected=3),
    negative=AddCase(x=-5, y=3, expected=-2),
)
def test_add_compact(params: AddCase) -> None:
    assert params.x + params.y == params.expected, "compact mode should expand fields"
# --8<-- [end:compact-mode]

# --8<-- [start:dict-mode]
@oxitest.parametrize(
    empty={"items": [], "expected": 0},
    one={"items": [42], "expected": 42},
    many={"items": [1, 2, 3], "expected": 6},
)
def test_sum(items: list[int], expected: int) -> None:
    assert sum(items) == expected, f"sum({items}) should equal {expected}"
# --8<-- [end:dict-mode]
# fmt: on


@dataclass(frozen=True)
class QueryCase:
    db: FixtureRef[object]
    query: str
    expected_rows: int


# fmt: off
# --8<-- [start:fixture-ref]
@oxitest.parametrize(
    real=QueryCase(db=db_conn, query="SELECT * FROM users", expected_rows=3),
    mock=QueryCase(db=mock_db, query="SELECT * FROM users", expected_rows=0),
)
def test_query(db: Fixture[object], query: str, expected_rows: int) -> None:
    rows = db.execute(query).fetchall()
    assert len(rows) == expected_rows, f"expected {expected_rows} rows"
# --8<-- [end:fixture-ref]

# --8<-- [start:composed]
@oxitest.parametrize(
    real=partial(QueryCase, db=real_db, expected_rows=3),
    mock=partial(QueryCase, db=mock_db, expected_rows=0),
)
@oxitest.parametrize(
    users=partial(QueryCase, query="SELECT * FROM users"),
    empty=partial(QueryCase, query="SELECT * FROM empty"),
)
def test_query_composed(db: Fixture[object], query: str, expected_rows: int) -> None:
    rows = db.execute(query).fetchall()
    assert len(rows) == expected_rows, f"expected {expected_rows} rows for {query}"
# --8<-- [end:composed]

# --8<-- [start:composed-compact]
@oxitest.parametrize(real=partial(QueryCase, db=real_db, expected_rows=3))
@oxitest.parametrize(users=partial(QueryCase, query="SELECT 1"))
def test_query_compact(db: Fixture[object], query: str, expected_rows: int) -> None:
    rows = db.execute(query).fetchall()
    assert len(rows) == expected_rows, "composed expanded should merge fields"
# --8<-- [end:composed-compact]
# fmt: on
