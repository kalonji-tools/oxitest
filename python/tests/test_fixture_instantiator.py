"""Unit tests for FixtureInstantiator — extracted resolution + creation chain."""

from __future__ import annotations

from typing import Any

from oxitest import Fixture, raises
from oxitest._bridge._errors import (
    AmbiguousFixtureError,
    BroadFixtureTypeError,
    FixtureCycleError,
    FixtureNotFoundError,
)
from oxitest._bridge._fixture_instantiator import (
    DispatchContext,
    FixtureInstantiator,
    ScopeRefs,
    _boundary_for,
    _ResolutionContext,
)
from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    FrameworkSource,
    ModuleSource,
    PluginSource,
)
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge._test_meta import TestMeta
from oxitest._bridge.plugin_loader import PluginRegistry
from tests import helpers


class _PluginType:
    """Marker type for plugin fixture tests."""


def _make_instantiator(
    *defs: FixtureDef,
) -> tuple[FixtureInstantiator, FixtureRegistry]:
    """Create an Instantiator + its registry."""
    reg = FixtureRegistry()
    for d in defs:
        reg.register(d)
    return FixtureInstantiator(reg, PluginRegistry()), reg


def test_resolve_simple_fixture() -> None:
    """resolve_fixture should return the factory's return value for a known fixture."""
    inst, _reg = _make_instantiator(
        helpers.make_fixture_def("db", lambda: "conn", declaration_path="/c.py")
    )
    teardowns: list = []

    ctx = _ResolutionContext(
        "test.py", teardowns, frozenset(), lambda _defn, _mod: None, "test.py"
    )
    result = inst.resolve_fixture("db", ctx)

    assert result == "conn", f"expected 'conn', got {result!r}"


def test_resolve_cycle_raises() -> None:
    """resolve_fixture raises FixtureCycleError when fixtures depend on each other."""

    def fx_a(b: Fixture[int]) -> None:
        pass

    def fx_b(a: Fixture[int]) -> None:
        pass

    inst, _reg = _make_instantiator(
        helpers.make_fixture_def("a", fx_a, declaration_path="/c.py"),
        helpers.make_fixture_def("b", fx_b, declaration_path="/c.py"),
    )

    with raises(FixtureCycleError):
        inst.resolve_fixture(
            "a",
            _ResolutionContext(
                "test.py", [], frozenset(), lambda _defn, _mod: None, "test.py"
            ),
        )


def test_resolve_not_found_raises() -> None:
    """resolve_fixture should raise FixtureNotFoundError for an unregistered name."""
    inst, _reg = _make_instantiator()

    with raises(FixtureNotFoundError):
        inst.resolve_fixture(
            "nope",
            _ResolutionContext(
                "test.py", [], frozenset(), lambda _defn, _mod: None, "test.py"
            ),
        )


def test_resolve_shared_uses_scope_refs() -> None:
    """Shared fixtures are stored in the ScopeRefs cache after first resolution."""
    inst, _reg = _make_instantiator(
        helpers.make_fixture_def(
            "shared_db", lambda: "shared_conn", declaration_path="/c.py", shared=True
        )
    )
    shared_cache: dict = {}
    shared_teardowns: list = []
    shared_hits: dict = {}
    shared_misses: dict = {}
    scope_refs = ScopeRefs(shared_cache, shared_teardowns, shared_hits, shared_misses)

    ctx = _ResolutionContext(
        "test.py", [], frozenset(), lambda _defn, _mod: scope_refs, "test.py"
    )
    inst.resolve_fixture("shared_db", ctx)

    assert "shared_db" in shared_cache, (
        f"expected 'shared_db' in cache, got {shared_cache}"
    )


def test_timing_recorded() -> None:
    """get_fixture_timings records setup_count and name for each resolved fixture."""
    inst, _reg = _make_instantiator(
        helpers.make_fixture_def("fast", lambda: 1, declaration_path="/c.py")
    )

    inst.resolve_fixture(
        "fast",
        _ResolutionContext(
            "test.py", [], frozenset(), lambda _defn, _mod: None, "test.py"
        ),
    )

    timings = inst.get_fixture_timings()
    assert len(timings) == 1, f"expected 1 timing entry, got {len(timings)}"
    assert timings[0].name == "fast", f"expected 'fast', got {timings[0].name!r}"
    assert timings[0].setup_count == 1, (
        f"expected 1 setup, got {timings[0].setup_count}"
    )


# ─── New error types ─────────────────────────────────────────────────────────


def test_ambiguous_fixture_error_lists_candidates() -> None:
    """AmbiguousFixtureError message lists candidate fixture names."""
    err = AmbiguousFixtureError("DBSession", ["dev_db", "prod_db"])
    msg = str(err)
    assert "DBSession" in msg, "error should mention the ambiguous type"
    assert "dev_db" in msg, "error should list candidate 'dev_db'"
    assert "prod_db" in msg, "error should list candidate 'prod_db'"


def test_broad_fixture_type_error() -> None:
    """BroadFixtureTypeError mentions the param name and broad type."""
    err = BroadFixtureTypeError("db", Any)
    msg = str(err)
    assert "db" in msg, "error should mention the parameter name"
    assert "Any" in msg, "error should mention the broad type"


# ─── Unified resolve_param ───────────────────────────────────────────────────


def test_resolve_param_by_type_not_name() -> None:
    """Conftest fixture resolves by binding type even when param name differs."""

    class MyType:
        pass

    defn = FixtureDef(
        name="my_thing",
        fixture_type=MyType,
        scope=FixtureScope.EACH,
        source=FrameworkSource(func=MyType, origin="/c.py"),
    )
    inst, _reg = _make_instantiator(defn)
    teardowns: list = []
    meta = TestMeta(module_path="t.py", fn_name="test_x", node_id="t.py::test_x")
    resolved, value = inst.resolve_param(
        "different_name",
        Fixture[MyType],
        DispatchContext(
            meta=meta,
            fn_teardowns=teardowns,
            resolve_user_fixture=lambda n: inst.resolve_fixture(
                n,
                _ResolutionContext(
                    "t.py", teardowns, frozenset(), lambda _defn, _mod: None, "t.py"
                ),
            ),
        ),
    )
    assert resolved is True, "should resolve by type"
    assert isinstance(value, MyType), (
        "should return a MyType instance from type-based resolution"
    )


def test_resolve_param_type_miss_name_fallback() -> None:
    """When type-based resolution fails but name matches, fall back to name."""

    class UnrelatedType:
        pass

    defn = helpers.make_fixture_def(
        "my_fixture",
        lambda: "value",
        declaration_path="/c.py",
        fixture_type=str,
    )
    inst, _reg = _make_instantiator(defn)
    teardowns: list = []
    meta = TestMeta(module_path="t.py", fn_name="test_x", node_id="t.py::test_x")

    resolved, value = inst.resolve_param(
        "my_fixture",
        Fixture[UnrelatedType],
        DispatchContext(
            meta=meta,
            fn_teardowns=teardowns,
            resolve_user_fixture=lambda n: inst.resolve_fixture(
                n,
                _ResolutionContext(
                    "t.py", teardowns, frozenset(), lambda _defn, _mod: None, "t.py"
                ),
            ),
        ),
    )
    assert resolved is True, "name fallback should resolve the fixture"
    assert value == "value", "should return the fixture value via name-based lookup"


def test_resolve_param_type_and_name_both_miss() -> None:
    """When both type and name resolution fail, raise FixtureNotFoundError."""

    class NoSuchType:
        pass

    inst, _reg = _make_instantiator()
    teardowns: list = []
    meta = TestMeta(module_path="t.py", fn_name="test_x", node_id="t.py::test_x")

    with raises(FixtureNotFoundError, match="no_such_fixture"):
        inst.resolve_param(
            "no_such_fixture",
            Fixture[NoSuchType],
            DispatchContext(
                meta=meta,
                fn_teardowns=teardowns,
                resolve_user_fixture=lambda _: None,
            ),
        )


def test_resolve_param_prefers_param_name_over_type_resolved_name() -> None:
    """FrameworkSource: prefer param name when both type and name match."""

    class DbConn:
        pass

    defn_primary = helpers.make_fixture_def(
        "db",
        DbConn,
        declaration_path="/c.py",
        fixture_type=DbConn,
    )
    inst, _reg = _make_instantiator(defn_primary)
    teardowns: list = []
    meta = TestMeta(module_path="t.py", fn_name="test_x", node_id="t.py::test_x")

    resolved_names: list[str] = []

    def tracking_resolve(name: str) -> DbConn:
        resolved_names.append(name)
        return DbConn()

    inst.resolve_param(
        "db",
        Fixture[DbConn],
        DispatchContext(
            meta=meta,
            fn_teardowns=teardowns,
            resolve_user_fixture=tracking_resolve,
        ),
    )
    assert resolved_names == ["db"], (
        "should resolve using param name 'db', not type-resolved name"
    )


# ─── resolve_by_source dispatch ────────────────────────────────────────────


def test_resolve_by_source_plugin() -> None:
    """PluginSource fixture resolved through registry dispatches to provider.create."""

    class FakeProvider:
        @property
        def name(self) -> str:
            return "fake"

        @property
        def fixture_type(self) -> type[_PluginType]:
            return _PluginType

        @property
        def scope(self) -> str:
            return "each"

        @property
        def autouse(self) -> bool:
            return False

        def create(self, **_: Any) -> str:
            return "plugin_value"

        def teardown(self, **_: Any) -> None:
            pass

    defn = FixtureDef(
        name="fake",
        fixture_type=_PluginType,
        scope=FixtureScope.EACH,
        source=PluginSource(provider=FakeProvider(), plugin_module="test_plugin"),
    )
    inst, _reg = _make_instantiator(defn)
    teardowns: list = []
    ctx = DispatchContext(
        meta=TestMeta(module_path="t.py", fn_name="test_x", node_id="t.py::test_x"),
        fn_teardowns=teardowns,
        resolve_user_fixture=lambda _: None,
    )
    value = inst.resolve_by_source(defn, ctx)
    assert value == "plugin_value", (
        "should return provider.create() result for PluginSource fixture"
    )
    assert len(teardowns) == 1, (
        "should register provider.teardown in teardowns list for cleanup"
    )


# ─── B1 boundary descent ─────────────────────────────────────────────────────


def test_boundary_switches_to_the_fixture_anchor_and_stops_at_unanchored_sources() -> (
    None
):
    """_boundary_for: ModuleSource replaces the boundary, everything else keeps it."""
    # Arrange
    anchored = FixtureDef(
        name="conn",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ModuleSource(
            func=lambda: None,
            defining_module_path="/t/api/__fixtures__.py",
            anchor_package_path="/t/api",
            lifetime=Lifetime.FUNCTION,
        ),
        namespace="api",
    )
    unanchored = FixtureDef(
        name="legacy",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=FrameworkSource(func=lambda: None, origin="/t/conftest.py"),
    )
    ctx = _ResolutionContext(
        "/t/api/v1/test_v1.py",
        [],
        frozenset(),
        lambda _defn, _mod: None,
        "/t/api/v1/test_v1.py",
    )

    # Act
    anchored_boundary = _boundary_for(anchored, ctx)
    unanchored_boundary = _boundary_for(unanchored, ctx)

    # Assert
    assert anchored_boundary == "/t/api", (
        "descending into a package fixture's dependencies must narrow the "
        "boundary to that package, which is ADR-0009 line 209's 'at "
        "declaration time' clause"
    )
    assert unanchored_boundary == "/t/api/v1/test_v1.py", (
        "conftest, plugin and builtin fixtures are exempt from B1, so they must "
        "leave the boundary alone rather than widening it to nothing or "
        "collapsing it to a path they do not have"
    )


def test_a_fixture_cannot_reach_a_dependency_below_its_own_anchor() -> None:
    """The narrowed boundary is what refuses the illegal dependency."""
    # Arrange — `thing` is anchored at /t/api/v1, one level below `conn`'s /t/api
    registry = FixtureRegistry()
    registry.register(
        FixtureDef(
            name="thing",
            fixture_type=object,
            scope=FixtureScope.EACH,
            source=ModuleSource(
                func=object,
                defining_module_path="/t/api/v1/__fixtures__.py",
                anchor_package_path="/t/api/v1",
                lifetime=Lifetime.FUNCTION,
            ),
            namespace="v1",
        )
    )

    # Act — the calling test lives in v1 and may use `thing` directly; a fixture
    # anchored at /t/api, resolving its own dependencies, may not.
    visible_to_test = registry.get_visible("thing", "/t/api/v1/test_v1.py")
    visible_to_conn = registry.get_visible("thing", "/t/api")

    # Assert
    assert visible_to_test is not None, (
        "the control: a test that genuinely lives in v1 must keep resolving "
        "v1's fixtures, or the fix has banned legal access along with illegal"
    )
    assert visible_to_conn is None, (
        "a fixture anchored at /t/api cannot legally declare a dependency on a "
        "fixture one level below it; resolving through the calling test's "
        "location let it acquire one anyway, and at lifetime='package' the "
        "cached value would then embed a value from the narrower boundary"
    )
