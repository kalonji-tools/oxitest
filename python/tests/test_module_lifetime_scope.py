"""Unit tests for module-lifetime scope caching (ADR-0009 slice 2).

Drives ``FixtureSession`` directly so each rule of the module tier can be
checked in isolation: one instance per module path, disposal at
``end_module``, and no cross-namespace collision. The end-to-end proof lives
in ``test_fixtures_redesign_slice2.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from oxitest import Fixture
from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
)
from oxitest._bridge._fixture_session import FixtureSession
from oxitest._bridge._lifetime import Lifetime

_MOD_A = "/proj/pkg/test_a.py"
_MOD_B = "/proj/pkg/test_b.py"


def _module_defn(
    name: str,
    func: Callable[..., Any],
    *,
    namespace: str = "pkg",
    anchor: str | None = None,
) -> FixtureDef[Any]:
    """Build a module-lifetime FixtureDef backed by *func*.

    *anchor* defaults to deriving ``/proj/{namespace}`` from the namespace,
    which is a convenience for tests that don't care about B1 visibility.
    Under B1, anchors and the module paths they're resolved from have to be
    genuinely coherent (the anchor must be the resolving module's own
    directory or an ancestor of it) — pass an explicit *anchor* when a test
    needs that relationship to hold.
    """
    return FixtureDef(
        name=name,
        fixture_type=object,
        scope=FixtureScope.MODULE,
        source=ModuleSource(
            func=func,
            defining_module_path=f"/proj/{namespace}/__fixtures__.py",
            anchor_package_path=anchor if anchor is not None else f"/proj/{namespace}",
            lifetime=Lifetime.MODULE,
        ),
        namespace=namespace,
    )


def _session_with(*defns: FixtureDef[Any]) -> FixtureSession:
    registry = FixtureRegistry()
    for defn in defns:
        registry.register(defn)
    return FixtureSession(registry)


def test_one_instance_per_module_path() -> None:
    """Repeated resolution within one module reuses the same instance."""
    calls: list[int] = []

    def resource() -> str:
        calls.append(1)
        return f"res-{len(calls)}"

    session = _session_with(_module_defn("resource", resource))
    teardowns: list[Callable[[], None]] = []

    first = session.get_fixture_in_namespace("resource", "pkg", _MOD_A, teardowns)
    second = session.get_fixture_in_namespace("resource", "pkg", _MOD_A, teardowns)

    assert len(calls) == 1, (
        f"factory ran {len(calls)} times for one module — module lifetime means "
        "one instance per module, so a second call here is a cache miss that "
        "silently makes the fixture per-test"
    )
    assert first == second, "both resolutions must hand back the same instance"


def test_distinct_instances_across_modules() -> None:
    """Each module path gets its own instance."""
    calls: list[str] = []

    def resource() -> str:
        calls.append("build")
        return f"res-{len(calls)}"

    session = _session_with(_module_defn("resource", resource))
    teardowns: list[Callable[[], None]] = []

    from_a = session.get_fixture_in_namespace("resource", "pkg", _MOD_A, teardowns)
    from_b = session.get_fixture_in_namespace("resource", "pkg", _MOD_B, teardowns)

    assert len(calls) == 2, (
        f"factory ran {len(calls)} times across two modules — module scope must "
        "not leak an instance from one module into the next"
    )
    assert from_a != from_b, (
        "two modules must get distinct instances; sharing one would make module "
        "lifetime behave like session lifetime"
    )


def test_end_module_drains_teardowns() -> None:
    """A yield fixture's teardown runs when its module ends, not before."""
    events: list[str] = []

    def resource() -> Iterator[str]:
        events.append("setup")
        yield "res"
        events.append("teardown")

    session = _session_with(_module_defn("resource", resource))
    teardowns: list[Callable[[], None]] = []

    session.get_fixture_in_namespace("resource", "pkg", _MOD_A, teardowns)
    assert events == ["setup"], (
        f"teardown fired early (events={events}) — module-lifetime teardown must "
        "wait for end_module, not run at resolution time"
    )

    session.end_module(_MOD_A)

    assert events == ["setup", "teardown"], (
        f"end_module did not drain the module scope (events={events}) — the "
        "fixture's cleanup never runs and whatever it holds leaks for the run"
    )


def test_end_module_pops_the_scope() -> None:
    """end_module must pop, not clear — otherwise scopes accumulate per module.

    A long run over many modules would otherwise retain one ``_Scope`` object
    for every module it ever touched.
    """

    def resource() -> str:
        return "res"

    session = _session_with(_module_defn("resource", resource))
    teardowns: list[Callable[[], None]] = []

    session.get_fixture_in_namespace("resource", "pkg", _MOD_A, teardowns)
    session.get_fixture_in_namespace("resource", "pkg", _MOD_B, teardowns)
    session.end_module(_MOD_A)
    session.end_module(_MOD_B)

    assert session._module_scopes == {}, (  # noqa: SLF001 — invariant is internal
        f"module scopes survived end_module ({list(session._module_scopes)}) — "  # noqa: SLF001
        "each one holds its module's cached instances, so the run's memory "
        "grows with the number of modules"
    )


def test_end_module_is_idempotent_for_unknown_paths() -> None:
    """end_module on a module that used no module-lifetime fixture is a no-op.

    The early-exit path in ``src/pipeline/execution.rs`` calls ``end_module``
    unconditionally, including for modules that never created a scope.
    """
    session = _session_with()

    session.end_module("/proj/pkg/never_seen.py")

    assert session._module_scopes == {}, (  # noqa: SLF001 — invariant is internal
        "draining an unseen module must not create a scope entry"
    )


def test_same_short_name_in_two_namespaces_stays_distinct() -> None:
    """Namespaces must not collide inside one module's scope.

    Module scope is the first path on which a namespaced fixture is cached.
    Keying the cache on the bare short name would hand ``sub.resource``
    whatever ``pkg.resource`` built first — a silently wrong instance.

    The two namespaces nest (``pkg`` and ``pkg/sub``) rather than sitting as
    unrelated siblings: under B1 an anchor and the module path resolved
    against it have to be genuinely coherent, and a nested pair is a tree that
    could actually exist on disk, resolved from one module inside the deeper
    package. That keeps the module-lifetime cache under test (one module
    path) while still proving namespace qualification (two distinct
    namespaces).
    """

    def resource_a() -> str:
        return "from-a"

    def resource_b() -> str:
        return "from-b"

    module_path = "/proj/pkg/sub/test_a.py"
    session = _session_with(
        _module_defn("resource", resource_a, namespace="pkg", anchor="/proj/pkg"),
        _module_defn("resource", resource_b, namespace="sub", anchor="/proj/pkg/sub"),
    )
    teardowns: list[Callable[[], None]] = []

    from_a = session.get_fixture_in_namespace("resource", "pkg", module_path, teardowns)
    from_b = session.get_fixture_in_namespace("resource", "sub", module_path, teardowns)

    assert from_a == "from-a", "pkg.resource must resolve to its own factory"
    assert from_b == "from-b", (
        f"sub.resource resolved to {from_b!r} — the module scope keyed both "
        "fixtures on the bare name 'resource', so the second lookup hit the "
        "first one's cache entry"
    )


def test_function_lifetime_still_uncached() -> None:
    """Slice-1 regression: function lifetime must stay per-resolution.

    Adding a cached tier must not accidentally route ``EACH`` through a scope.
    """
    calls: list[int] = []

    def resource() -> str:
        calls.append(1)
        return "res"

    each_defn = FixtureDef(
        name="resource",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ModuleSource(
            func=resource,
            defining_module_path="/proj/pkg/__fixtures__.py",
            anchor_package_path="/proj/pkg",
            lifetime=Lifetime.FUNCTION,
        ),
        namespace="pkg",
    )
    session = _session_with(each_defn)
    teardowns: list[Callable[[], None]] = []

    session.get_fixture_in_namespace("resource", "pkg", _MOD_A, teardowns)
    session.get_fixture_in_namespace("resource", "pkg", _MOD_A, teardowns)

    assert len(calls) == 2, (
        f"function-lifetime fixture built {len(calls)} time(s) for two "
        "resolutions — slice 1's per-test semantics regressed into caching"
    )


def test_module_fixture_may_depend_on_another_module_fixture() -> None:
    """A module-scope fixture depending on another resolves and tears down LIFO.

    Both live in the same module scope, so the dependency must be built first
    and disposed last — the reverse of the drain order would tear down the
    inner fixture while the outer one still holds it.
    """
    events: list[str] = []
    injected: list[str] = []

    def inner() -> Iterator[str]:
        events.append("inner-setup")
        yield "inner"
        events.append("inner-teardown")

    def outer(inner: Fixture[str]) -> Iterator[str]:
        events.append("outer-setup")
        # Recorded rather than interpolated: cached fixture values arrive
        # wrapped in FrozenProxy, which has no __str__, so an f-string here
        # would compare against "FrozenProxy('inner')".
        injected.append(inner)
        yield "outer"
        events.append("outer-teardown")

    session = _session_with(_module_defn("inner", inner), _module_defn("outer", outer))
    teardowns: list[Callable[[], None]] = []

    value = session.get_fixture_in_namespace("outer", "pkg", _MOD_A, teardowns)
    assert value == "outer", (
        f"outer fixture resolved to {value!r} — expected its own yielded value"
    )
    assert injected == ["inner"], (
        f"dependency was not injected — outer saw {injected}; a module-scope "
        "fixture must be able to depend on another one in the same scope"
    )

    session.end_module(_MOD_A)

    assert events == [
        "inner-setup",
        "outer-setup",
        "outer-teardown",
        "inner-teardown",
    ], (
        f"teardown order was {events} — the scope drains in reverse, so the "
        "dependency must outlive its dependent"
    )


def test_failing_module_teardown_does_not_abort_the_drain() -> None:
    """One raising teardown must not strand the rest of the module's scope.

    ``_Scope.drain`` routes failures through ``safe_teardown``, which reports
    and continues. Without that, a single bad teardown would leak every
    fixture disposed after it.
    """
    events: list[str] = []

    def boom() -> Iterator[str]:
        yield "boom"
        events.append("boom-teardown")
        msg = "teardown boom"
        raise RuntimeError(msg)

    def survivor() -> Iterator[str]:
        yield "survivor"
        events.append("survivor-teardown")

    session = _session_with(
        _module_defn("survivor", survivor), _module_defn("boom", boom)
    )
    teardowns: list[Callable[[], None]] = []

    session.get_fixture_in_namespace("survivor", "pkg", _MOD_A, teardowns)
    session.get_fixture_in_namespace("boom", "pkg", _MOD_A, teardowns)

    session.end_module(_MOD_A)

    assert events == ["boom-teardown", "survivor-teardown"], (
        f"drain stopped early (events={events}) — a raising teardown must be "
        "reported and stepped over, not allowed to strand the fixtures queued "
        "behind it"
    )
