"""Unit tests for FixtureInstantiator — extracted resolution + creation chain."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest._bridge._helper_namespace import HelperNamespace

    helpers: HelperNamespace

from conftest import helpers  # type: ignore[assignment]
from oxitest import Fixture, raises
from oxitest._bridge._errors import (
    AmbiguousFixtureError,
    BroadFixtureTypeError,
    FixtureCycleError,
    FixtureNotFoundError,
)
from oxitest._bridge._fixture_instantiator import FixtureInstantiator, ScopeRefs
from oxitest._bridge._fixture_registry import FixtureRegistry
from oxitest._bridge.plugin_loader import PluginRegistry


def _make_instantiator(*defs) -> tuple[FixtureInstantiator, FixtureRegistry]:
    """Create an Instantiator + its registry."""
    reg = FixtureRegistry()
    for d in defs:
        reg.register(d)
    return FixtureInstantiator(reg, PluginRegistry()), reg


def test_resolve_simple_fixture():
    inst, _reg = _make_instantiator(
        helpers.common.make_fixture_def("db", lambda: "conn", conftest_path="/c.py")
    )
    teardowns: list = []

    result = inst.resolve_fixture(
        "db", "test.py", teardowns, frozenset(), lambda _defn: None
    )

    assert result == "conn", f"expected 'conn', got {result!r}"


def test_resolve_cycle_raises():
    def fx_a(b: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    def fx_b(a: Fixture[int]) -> None:  # type: ignore[type-arg]
        pass

    inst, _reg = _make_instantiator(
        helpers.common.make_fixture_def("a", fx_a, conftest_path="/c.py"),
        helpers.common.make_fixture_def("b", fx_b, conftest_path="/c.py"),
    )

    with raises(FixtureCycleError):
        inst.resolve_fixture("a", "test.py", [], frozenset(), lambda _defn: None)


def test_resolve_not_found_raises():
    inst, _reg = _make_instantiator()

    with raises(FixtureNotFoundError):
        inst.resolve_fixture("nope", "test.py", [], frozenset(), lambda _defn: None)


def test_resolve_shared_uses_scope_refs():
    inst, _reg = _make_instantiator(
        helpers.common.make_fixture_def(
            "shared_db", lambda: "shared_conn", conftest_path="/c.py", shared=True
        )
    )
    shared_cache: dict = {}
    shared_teardowns: list = []
    shared_hits: dict = {}
    shared_misses: dict = {}
    scope_refs = ScopeRefs(shared_cache, shared_teardowns, shared_hits, shared_misses)

    inst.resolve_fixture(
        "shared_db", "test.py", [], frozenset(), lambda _defn: scope_refs
    )

    assert "shared_db" in shared_cache, (
        f"expected 'shared_db' in cache, got {shared_cache}"
    )


def test_timing_recorded():
    inst, _reg = _make_instantiator(
        helpers.common.make_fixture_def("fast", lambda: 1, conftest_path="/c.py")
    )

    inst.resolve_fixture("fast", "test.py", [], frozenset(), lambda _defn: None)

    timings = inst.get_fixture_timings()
    assert len(timings) == 1, f"expected 1 timing entry, got {len(timings)}"
    assert timings[0].name == "fast", f"expected 'fast', got {timings[0].name!r}"
    assert timings[0].setup_count == 1, (
        f"expected 1 setup, got {timings[0].setup_count}"
    )


# ─── New error types ─────────────────────────────────────────────────────────


def test_ambiguous_fixture_error_lists_candidates():
    """AmbiguousFixtureError message lists candidate fixture names."""
    err = AmbiguousFixtureError("DBSession", ["dev_db", "prod_db"])
    msg = str(err)
    assert "DBSession" in msg, "error should mention the ambiguous type"
    assert "dev_db" in msg, "error should list candidate 'dev_db'"
    assert "prod_db" in msg, "error should list candidate 'prod_db'"


def test_broad_fixture_type_error():
    """BroadFixtureTypeError mentions the param name and broad type."""
    from typing import Any

    err = BroadFixtureTypeError("db", Any)
    msg = str(err)
    assert "db" in msg, "error should mention the parameter name"
    assert "Any" in msg, "error should mention the broad type"
