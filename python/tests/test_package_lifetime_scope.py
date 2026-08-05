"""Unit tests for package-lifetime scope caching (ADR-0009 slice 3, #1710).

Drives ``FixtureSession`` directly so each rule of the package tier can be
checked in isolation: one instance per anchor directory regardless of which
module asks, and disposal at ``end_package`` rather than ``end_module``. The
end-to-end proof — that the guarantee survives parallel execution — lives in
``test_fixtures_redesign_slice3.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._lifetime import Lifetime

_ANCHOR = "/proj/api"
_MOD_A = "/proj/api/test_a.py"
_MOD_B = "/proj/api/test_b.py"
_SUB_MOD = "/proj/api/v1/test_c.py"
_OTHER_ANCHOR = "/proj/core"
_OTHER_MOD = "/proj/core/test_d.py"


def _package_defn(
    name: str,
    func: Callable[..., Any],
    *,
    anchor: str = _ANCHOR,
    namespace: str = "api",
) -> FixtureDef[Any]:
    """Build a package-lifetime FixtureDef anchored at *anchor*."""
    return FixtureDef(
        name=name,
        fixture_type=object,
        scope=FixtureScope.PACKAGE,
        source=ModuleSource(
            func=func,
            defining_module_path=f"{anchor}/__fixtures__.py",
            anchor_package_path=anchor,
            lifetime=Lifetime.PACKAGE,
        ),
        namespace=namespace,
    )


def _session_with(*defns: FixtureDef[Any]) -> FixtureSession:
    registry = FixtureRegistry()
    for defn in defns:
        registry.register(defn)
    return FixtureSession(registry)


def test_one_instance_across_modules_in_the_anchor() -> None:
    """Two modules under the same anchor share one instance."""
    # Arrange
    calls: list[int] = []

    def engine() -> str:
        calls.append(1)
        return f"engine-{len(calls)}"

    session = _session_with(_package_defn("engine", engine))
    teardowns: list[Callable[[], None]] = []

    # Act
    from_a = session.get_fixture_in_namespace(
        "engine", "api", _MOD_A, teardowns, test_is_async=True
    )
    from_b = session.get_fixture_in_namespace(
        "engine", "api", _MOD_B, teardowns, test_is_async=True
    )

    # Assert — keying on module_path, as the module tier does, would build one
    # instance per module. That is the silent duplicate the whole tier exists to
    # prevent, and it would look identical to lifetime="module" in practice.
    assert len(calls) == 1, (
        f"factory ran {len(calls)} times across two modules in one package — "
        "package lifetime means one instance per anchor, not per module"
    )
    assert from_a == from_b, "both modules must hand back the same instance"


def test_descendant_packages_share_the_ancestor_instance() -> None:
    """A module in a subpackage sees the ancestor's instance."""
    # Arrange
    calls: list[int] = []

    def engine() -> str:
        calls.append(1)
        return f"engine-{len(calls)}"

    session = _session_with(_package_defn("engine", engine))
    teardowns: list[Callable[[], None]] = []

    # Act
    from_top = session.get_fixture_in_namespace(
        "engine", "api", _MOD_A, teardowns, test_is_async=True
    )
    from_sub = session.get_fixture_in_namespace(
        "engine", "api", _SUB_MOD, teardowns, test_is_async=True
    )

    # Assert — B1 makes a package fixture usable from descendant packages, so a
    # subpackage resolving its own instance would violate the boundary rule as
    # well as the exactly-once guarantee.
    assert len(calls) == 1, (
        f"factory ran {len(calls)} times — a descendant package must reuse the "
        "ancestor's instance, not build its own"
    )
    assert from_top == from_sub, "subpackage must see the ancestor's instance"


def test_distinct_anchors_get_distinct_instances() -> None:
    """Two sibling packages each get their own instance."""
    # Arrange
    calls: list[str] = []

    def engine() -> str:
        calls.append("build")
        return f"engine-{len(calls)}"

    session = _session_with(
        _package_defn("engine", engine),
        _package_defn("engine", engine, anchor=_OTHER_ANCHOR, namespace="core"),
    )
    teardowns: list[Callable[[], None]] = []

    # Act
    from_api = session.get_fixture_in_namespace(
        "engine", "api", _MOD_A, teardowns, test_is_async=True
    )
    from_core = session.get_fixture_in_namespace(
        "engine", "core", _OTHER_MOD, teardowns, test_is_async=True
    )

    # Assert — sharing across anchors would make package lifetime behave like
    # session lifetime, and leak state between unrelated parts of a suite.
    assert len(calls) == 2, (
        f"factory ran {len(calls)} times across two anchors — each package "
        "boundary owns its own instance"
    )
    assert from_api != from_core, "sibling packages must not share an instance"


def test_end_package_drains_teardowns() -> None:
    """A yield fixture's teardown runs at end_package, not end_module."""
    # Arrange
    events: list[str] = []

    def engine() -> Iterator[str]:
        events.append("setup")
        yield "engine"
        events.append("teardown")

    session = _session_with(_package_defn("engine", engine))
    teardowns: list[Callable[[], None]] = []
    session.get_fixture_in_namespace(
        "engine", "api", _MOD_A, teardowns, test_is_async=True
    )

    # Act — a module in the package ends, but the package has not.
    session.end_module(_MOD_A)
    after_module = list(events)
    session.end_package(_ANCHOR)

    # Assert — tearing down at end_module would make the value unavailable to
    # every later module in the package, which is exactly what the tier promises
    # to keep alive.
    assert after_module == ["setup"], (
        f"teardown ran at end_module, events={after_module} — a package-lifetime "
        "value must survive its modules ending"
    )
    assert events == ["setup", "teardown"], (
        f"end_package must drain the teardown, events={events}"
    )


def test_end_package_is_inert_for_an_unknown_anchor() -> None:
    """Draining a package that never resolved anything is a no-op."""
    # Arrange
    session = _session_with(_package_defn("engine", lambda: "engine"))

    # Act
    session.end_package("/proj/never-used")

    # Assert — an anchored group fires end_package whether or not its package
    # fixtures were actually resolved: one module's declaration co-locates the
    # whole subtree, and no test in it need have asked for the fixture. Raising
    # there would abort a clean run.
    #
    # This does NOT bless a permanent miss. Before #1839 every call landed here,
    # because end_package was handed a module path against an anchor-keyed dict,
    # and this test read as if that were the intended shape. What proves the hit
    # happens is test_1839_package_boundary.py, end to end — a unit test that
    # hands the anchor over by hand cannot see which value the caller chooses.
    assert session.get_cache_stats() is not None, (
        "end_package on an unused anchor must leave the session usable"
    )
