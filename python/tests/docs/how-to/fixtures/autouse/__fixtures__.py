"""Isolated conftest for the autouse fixture example.

Kept in its own subdirectory to separate this autouse fixture from the
other fixture examples. That separation is organisational, not enforced:
conftest fixtures are registered run-wide rather than scoped to their own
subtree (#1760).
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


@oxitest.fixture(lifetime="function")
def db() -> _Connection:
    return _connect()


# fmt: off
# --8<-- [start:autouse-fixture]
@oxitest.fixture(lifetime="function", autouse=True)
def reset_database(db: Fixture[_Connection]) -> Generator[None, None, None]:
    yield
    db.execute("DELETE FROM users")
# --8<-- [end:autouse-fixture]
# fmt: on
