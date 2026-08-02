"""Every legal cell of ADR-0006's async table, on the ``Fixture[T]`` route.

Seven tests: four async (one per lifetime tier) and three sync (the three
tiers wider than ``function``). The count is asserted by the runner test so a
collection regression that dropped half the matrix cannot pass vacuously.

The sync half is the load-bearing part. It is what makes "refuse the parameter
route the way the proxy route is refused" a *visible* regression rather than a
free tightening.
"""

from __future__ import annotations

from oxitest import Fixture

from agm._kinds import Fn, Mod, Pkg, Sess


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
