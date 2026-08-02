"""Fixtures for how-to doc examples.

Provides stub fixtures referenced by parametrize and fixture examples.

No page under ``docs/user/`` sources a ``--8<--`` snippet from this file — #1720
deletes it, so an example anchored here would take its page down with it (#1869).
"""

from oxitest import Fixtures

fx = Fixtures()


class _StubConnection:
    """Minimal DB connection stub for doc examples."""

    def __init__(self, *, rows: list) -> None:
        self._rows = rows

    def execute(self, query: str):
        return self

    def fetchall(self):
        return self._rows


@fx.fixture
def db_conn() -> _StubConnection:
    """Real DB stub — returns 3 rows."""
    return _StubConnection(rows=[1, 2, 3])


@fx.fixture
def mock_db() -> _StubConnection:
    """Mock DB stub — returns 0 rows."""
    return _StubConnection(rows=[])


@fx.fixture
def real_db() -> _StubConnection:
    """Real DB stub — alias for db_conn pattern."""
    return _StubConnection(rows=[1, 2, 3])
