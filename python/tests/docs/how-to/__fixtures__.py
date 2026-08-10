"""Fixtures for how-to doc examples.

Provides stub fixtures referenced by parametrize and fixture examples.

No page under ``docs/user/`` sources a ``--8<--`` snippet from this file — #1720
deletes it, so an example anchored here would take its page down with it (#1869).
"""

import oxitest


class _StubConnection:
    """Minimal DB connection stub for doc examples."""

    def __init__(self, *, rows: list) -> None:
        self._rows = rows

    def execute(self, query: str):
        return self

    def fetchall(self):
        return self._rows


@oxitest.fixture(lifetime="function")
def db_conn() -> _StubConnection:
    """Real DB stub — returns 3 rows."""
    return _StubConnection(rows=[1, 2, 3])


@oxitest.fixture(lifetime="function")
def mock_db() -> _StubConnection:
    """Mock DB stub — returns 0 rows."""
    return _StubConnection(rows=[])


@oxitest.fixture(lifetime="function")
def real_db() -> _StubConnection:
    """Real DB stub — alias for db_conn pattern."""
    return _StubConnection(rows=[1, 2, 3])
