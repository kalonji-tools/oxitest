"""A sync test whose ``FixtureRef`` resolves to a namespaced async fixture.

Both parametrize cases are expected to error. Before #1876's fail-closed
change the executor omitted ``test_is_async``, inherited the permissive
default, and injected an ``AsyncFixtureHandle`` — an object the sync test can
neither await nor use, and whose attribute access reports a fixture problem
rather than a test-kind one.

The fixtures are imported from ``agi._registrar``, not from
``agi.conftest`` — see that module's docstring for why function identity makes
the difference between exercising this route and silently falling back to the
un-namespaced one.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest import Fixture, FixtureRef, Fixtures

from agi._kinds import Conn
from agi._registrar import conn, other


@dataclass(frozen=True)
class Case:
    """One parametrize case: the fixture this test asks to be handed."""

    c: FixtureRef[Conn]


@oxi.parametrize(first=Case(c=conn), second=Case(c=other))
def test_sync_fixture_ref_to_an_async_fixture(c: Fixture[Conn]) -> None:
    # Assert — unreachable; resolution above must have raised
    assert c.label != "", (
        "reaching this line means a sync test was handed an AsyncFixtureHandle "
        "and read an attribute off it, which is the silent failure #1876 "
        "reports rather than the loud refusal ADR-0006 asks for"
    )


async def test_an_async_test_reaches_the_same_fixture(fx: Fixtures) -> None:
    """Positive control for the refusal above.

    Without it, a sync failure would prove only that the fixture was never
    registered.
    """
    # Act
    value = await fx.conn

    # Assert
    assert value.label == "conn", (
        "the namespaced async fixture must be reachable from an async test; if "
        "it is not, the sync refusal above is measuring a registration failure "
        "rather than the async guard"
    )
