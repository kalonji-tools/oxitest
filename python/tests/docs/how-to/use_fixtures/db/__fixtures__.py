"""The `db` anchor for the use-fixtures how-to guide.

The guide's `fx.db.conn` example reads a fixture named `conn` under the anchor
`db`. The anchor is the directory, so the declaration lives here and the test
that reads it sits in this package — a fixture is visible only to tests in its
anchor package or below (ADR-0009 Rule 3).
"""

import oxitest


class Connection:
    """Stub DB connection reached as `fx.db.conn`."""

    def export(self) -> str:
        return '{"data": []}'


@oxitest.fixture(lifetime="function")
def conn() -> Connection:
    return Connection()
