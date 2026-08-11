"""Every legal cell of ADR-0006's async table, on the parameter routes.

Nine tests: four async (one per lifetime tier), three sync (the three tiers
wider than ``function``), and two ``FixtureRef`` cases, which ADR-0006
Amendment 2 made legal. The count is asserted by the runner test so a
collection regression that dropped half the matrix cannot pass vacuously.

The sync half is the load-bearing part. It is what makes "refuse the parameter
route the way the proxy route is refused" a *visible* regression rather than a
free tightening.

The proxy route is **not** here. It refuses a sync test at every lifetime, and
those cells live in ``../illegal/``.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi
from oxitest import Fixture, FixtureRef

from agm._kinds import Fn, Mod, Pkg, Ref, Sess
from agm._refs import by_ref


async def test_async_reaches_function_lifetime(per_function: Fixture[Fn]) -> None:
    assert per_function.label == "fn", (
        "a function-lifetime async fixture reaches an async test on the "
        "per-test loop; a raw coroutine here means the injection path stopped "
        "advancing it"
    )


async def test_async_reaches_module_lifetime(per_module: Fixture[Mod]) -> None:
    assert per_module.label == "mod", (
        "module lifetime resolves on the shared session loop before the test "
        "runs; a coroutine here means the eager path was skipped"
    )


async def test_async_reaches_package_lifetime(per_package: Fixture[Pkg]) -> None:
    assert per_package.label == "pkg", (
        "package lifetime shares the shared session loop with module lifetime "
        "— covering only module would leave the tier ADR-0009 added untested"
    )


async def test_async_reaches_session_lifetime(per_session: Fixture[Sess]) -> None:
    assert per_session.label == "sess", (
        "session lifetime is the widest tier and the one whose teardown is "
        "clamped to loop lifetime; a failure here is a loop-ownership bug, not "
        "an injection bug"
    )


def test_sync_reaches_module_lifetime(per_module: Fixture[Mod]) -> None:
    assert per_module.label == "mod", (
        "ADR-0006 routes module lifetime through SharedAsyncManager.resolve(), "
        "so the value was already awaited when the sync test started — there "
        "is nothing left for the test to await and nothing to refuse"
    )


def test_sync_reaches_package_lifetime(per_package: Fixture[Pkg]) -> None:
    assert per_package.label == "pkg", (
        "same dispatch as module lifetime; asserted separately because a guard "
        "keyed on lifetime rather than on dispatch could plausibly split them"
    )


def test_sync_reaches_session_lifetime(per_session: Fixture[Sess]) -> None:
    assert per_session.label == "sess", (
        "the widest legal sync cell; if a future change refuses async fixtures "
        "to sync tests wholesale, this is the test that says so out loud "
        "instead of letting the ADR quietly drift"
    )


# ── the FixtureRef route, legal since ADR-0006 Amendment 2 ────────────────────
#
# `FixtureRef` names a fixture in a parametrize case and delivers it as a
# parameter, so it owes the answer `Fixture[T]` injection gives. It used to
# resolve through the proxy machinery instead and raised for a sync test at
# every tier, which left one cell with two answers depending on how the fixture
# was named (#1876).
#
# `module` lifetime only, and that is a property of the route rather than a
# gap: a `FixtureRef` needs the fixture *function object*, so the test module
# imports it, which registers it as an inline declaration anchored here.
# ADR-0009 Rule 1 caps an inline declaration at `module`, so the wider tiers
# cannot be expressed on this route at all. `_refs.py` says why the fixture is
# declared there rather than in `__fixtures__.py`. The `function` tier stays
# illegal and lives in `../illegal/`.


@dataclass(frozen=True)
class RefCase:
    """One parametrize case naming a module-lifetime async fixture."""

    c: FixtureRef[Ref]


@oxi.parametrize(first=RefCase(c=by_ref), second=RefCase(c=by_ref))
def test_sync_reaches_module_lifetime_by_fixture_ref(c: Fixture[Ref]) -> None:
    assert c.label == "ref", (
        "a FixtureRef delivers a parameter, so a sync test must receive the "
        "awaited value exactly as Fixture[T] injection does; an "
        "AsyncFixtureAccessError here means the route went back through the "
        "proxy guard and cited an fx. spelling the user never wrote"
    )
