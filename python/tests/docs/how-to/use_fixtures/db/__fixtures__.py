"""The `db` anchor for the use-fixtures how-to guide.

The guide's `fx.db.conn` example reads a fixture named `conn` under the anchor
`db`. The anchor is the directory, so the declaration lives here and the test
that reads it sits in this package — a fixture is visible only to tests in its
anchor package or below (ADR-0009 Rule 3).
"""

import oxitest
from oxitest import Yields


class Connection:
    """Stub DB connection reached as `fx.db.conn`."""

    def export(self) -> str:
        return '{"data": []}'


@oxitest.fixture(lifetime="function")
def conn() -> Connection:
    return Connection()


#: The table the guide's `reset_database` fixture refills. Module-level so a
#: test that *arranges* the fixture can observe it ran: `@oxi.arrange`
#: discards the fixture's value, so a side effect is the only evidence (#2111).
#:
#: The fixture appends and its teardown clears. That catches a broken arrange
#: (the list is empty) and a broken teardown (the next test sees two rows) —
#: a fixed sentinel value would let the second test pass on the first one's
#: residue.
_ROWS: list[str] = []

#: Same shape, for the second arranged fixture.
_CONFIGS: list[str] = []


@oxitest.fixture(lifetime="function")
def rows() -> list[str]:
    """The table's contents, reached as `fx.db.rows`."""
    return _ROWS


@oxitest.fixture(lifetime="function")
def loaded_config() -> list[str]:
    """What `app_config` loaded, reached as `fx.db.loaded_config`."""
    return _CONFIGS


@oxitest.fixture(lifetime="function")
def reset_database() -> Yields[None]:
    """Side-effect-only fixture, reached by `@oxi.arrange` in the guide."""
    _ROWS.append("fresh")
    yield
    _ROWS.clear()


@oxitest.fixture(lifetime="function")
def app_config() -> Yields[dict]:
    """Also side-effect bearing, for the same reason as `reset_database`.

    Function lifetime rather than package: a package fixture is built once for
    the whole package, so a test that arranges it cannot tell whether it ran
    for that test or for an earlier one.
    """
    _CONFIGS.append("loaded")
    yield {"db_url": "sqlite:///:memory:", "debug": True}
    _CONFIGS.clear()
