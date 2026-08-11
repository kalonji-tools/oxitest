"""The cells a sync test still cannot reach, after ADR-0006 Amendment 2.

Every test here is expected to error. The acceptance test reads the run's exit
code, so a cell that quietly became legal shows up as a passing run rather than
as a silent gap.

Two different refusals, on purpose:

- the ``FixtureRef`` case is refused by ``AsyncDepGuardMiddleware``, which sees
  an un-advanced coroutine in the resolved kwargs. Only ``function`` lifetime
  produces one.
- the proxy cases are refused by ``AsyncFixtureAccessError``, at *every*
  lifetime. That is the strictness Amendment 2 records, and it is stricter than
  ADR-0006's own cell.

The pairing is the point. At ``module`` lifetime the same fixture is legal
through ``Fixture[T]`` and through ``FixtureRef`` (see ``../legal/``) and
illegal through the proxy. One fixture, one test kind, two answers — decided by
the access route, which is exactly what Amendment 2 states.
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
def test_sync_fixture_ref_to_a_function_lifetime_async_fixture(
    c: Fixture[Conn],
) -> None:
    # Assert — unreachable; resolution above must have raised
    assert c.label != "", (
        "a function-lifetime async fixture reaches the test as an un-advanced "
        "coroutine, and AsyncDepGuardMiddleware must refuse it; reaching this "
        "line means a sync test is holding a coroutine it cannot await"
    )


def test_sync_proxy_at_module_lifetime(fx: Fixtures) -> None:
    # Assert — unreachable; the proxy access above must have raised
    assert fx.wide_module.label != "", (
        "the proxy hands back a handle only `await` can unwrap, so it refuses a "
        "sync test even at a lifetime where the Fixture[T] route succeeds; "
        "reaching this line means the proxy stopped being stricter than "
        "ADR-0006's cell and Amendment 2 is no longer true"
    )


def test_sync_proxy_at_package_lifetime(fx: Fixtures) -> None:
    # Assert — unreachable; the proxy access above must have raised
    assert fx.wide_package.label != "", (
        "asserted separately from module lifetime because a guard keyed on the "
        "tier rather than on the route could plausibly split them, and package "
        "is the widest tier reachable outside a rootdir package"
    )


async def test_an_async_test_reaches_the_same_fixture(fx: Fixtures) -> None:
    """Positive control for the refusals above.

    Without it, a failure would prove only that the fixtures were never
    registered.
    """
    # Act
    value = await fx.wide_module

    # Assert
    assert value.label == "wide-module", (
        "the async test must reach the same fixture the sync ones are refused; "
        "if it cannot, the refusals above are measuring a registration failure "
        "rather than the async guard"
    )


def test_sync_proxy_at_function_lifetime(fx: Fixtures) -> None:
    """The function tier, on the proxy route, for the *hint* it prints.

    ``conn`` is declared in ``_registrar.py`` and imported above, so it is an
    inline declaration anchored at this module and visible to it. Reaching it
    through the proxy is illegal for the same reason the wider tiers are.

    This cell exists to pin the two-way form of the diagnostic. Raising the
    lifetime is what makes the *parameter* route work, so offering it here
    would send the reader to a change that cannot help them.
    """
    # Assert — unreachable; the proxy access above must have raised
    assert fx.conn.label != "", (
        "a function-lifetime async fixture is illegal on every route; reaching "
        "this line means the narrowest cell stopped being refused"
    )


def test_sync_qualified_proxy_at_module_lifetime(fx: Fixtures) -> None:
    """The *qualified* proxy spelling, which is a different guard site.

    ``fx.<name>`` resolves through ``get_fixture_shortcut`` and
    ``fx.<namespace>.<name>`` through ``get_fixture_in_namespace``. Each site
    raises independently, so covering only the shortcut left the qualified
    guard unpinned — a mutation that relaxed it to fire at ``function``
    lifetime alone survived the whole suite.
    """
    # Assert — unreachable; the qualified access above must have raised
    assert fx.agi.wide_module.label != "", (
        "the qualified proxy route must refuse a sync test at a wider tier "
        "exactly as the shortcut does; if the two disagree, one spelling of "
        "the same access became legal"
    )
