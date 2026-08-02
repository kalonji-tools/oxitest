"""Isolated conftest for the autouse fixture example.

Lives in its own subdirectory to bound which tests the autouse fixture
applies to — a conftest's fixtures are visible to every test at or below
its own directory, so an autouse fixture declared higher up would fire
for unrelated examples.
"""

from collections.abc import Generator

import oxitest
from oxitest import Fixture


class _Connection:
    """Stub DB connection for autouse example."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(self, sql: str) -> None:
        self.calls.append(sql)


def _connect() -> _Connection:
    return _Connection()


fx = oxitest.Fixtures()


@fx.fixture
def db() -> _Connection:
    return _connect()


# fmt: off
# --8<-- [start:autouse-fixture]
@fx.fixture(autouse=True)
def reset_database(db: Fixture[_Connection]) -> Generator[None, None, None]:
    yield
    db.execute("DELETE FROM users")
# --8<-- [end:autouse-fixture]
# fmt: on
