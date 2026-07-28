"""Tests for module-source fixture registrar.

Task 6 of the fixture-redesign slice-1 plan. Scans a module for
@oxi.fixture-decorated functions, derives namespace from anchor-package
segment name, and registers each into FixtureRegistry as a
ModuleSource-backed FixtureDef. Enforces loud collision detection.
"""

from __future__ import annotations

import types

from oxitest import TempDir, raises
from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_decorator import fixture
from oxitest._bridge._fixture_registry import (
    ConftestSource,
    FixtureDef,
    FixtureRegistry,
    FixtureScope,
    ModuleSource,
)
from oxitest._bridge._lifetime import Lifetime
from oxitest._bridge._module_source_registrar import (
    register_module_source_fixtures,
)


def _make_fake_fixture_module(tmp: TempDir) -> types.ModuleType:
    """Fabricate an in-memory module with one @oxi.fixture-decorated func."""
    mod = types.ModuleType("slice1_pkg.__fixtures__")
    mod.__file__ = str(tmp / "slice1_pkg" / "__fixtures__.py")

    @fixture(lifetime="function")
    def conn() -> object:
        return object()

    setattr(mod, "conn", conn)  # noqa: B010 — dynamic module attr
    return mod


def test_registers_decorated_functions(tmp: TempDir) -> None:
    """Decorated functions land in the registry with correct source and scope."""
    registry = FixtureRegistry()
    (tmp / "slice1_pkg").mkdir()
    mod = _make_fake_fixture_module(tmp)

    register_module_source_fixtures(
        registry, mod, anchor_package_path=str(tmp / "slice1_pkg")
    )

    defn = registry.get_in_namespace("conn", "slice1_pkg")
    assert defn is not None, (
        "fixture must land in the registry after register_module_source_fixtures"
    )
    assert isinstance(defn.source, ModuleSource), (
        "registered fixture must have ModuleSource, not ConftestSource"
    )
    assert defn.source.lifetime is Lifetime.FUNCTION, (
        "ModuleSource must retain the lifetime tier passed to the decorator"
    )
    assert defn.scope is FixtureScope.EACH, (
        "Lifetime.FUNCTION must map to FixtureScope.EACH so existing per-test "
        "cache/teardown semantics apply"
    )


def test_declared_lifetime_reaches_fixture_def_scope(tmp: TempDir) -> None:
    """A module-lifetime declaration must survive into FixtureDef.scope.

    Slice 1 hardcoded ``scope=FixtureScope.EACH`` here. The tier survived on
    ``ModuleSource.lifetime`` but the caching machinery reads ``FixtureDef.scope``,
    so every declaration was silently function-scoped. That was invisible while
    ``"function"`` was the only legal value — this test is the tripwire that
    keeps the mapping wired as slices 3 and 4 add tiers.
    """
    registry = FixtureRegistry()
    (tmp / "slice2_pkg").mkdir()

    mod = types.ModuleType("slice2_pkg.__fixtures__")
    mod.__file__ = str(tmp / "slice2_pkg" / "__fixtures__.py")

    @fixture(lifetime="module")
    def conn() -> object:
        return object()

    setattr(mod, "conn", conn)  # noqa: B010 — dynamic module attr

    register_module_source_fixtures(
        registry, mod, anchor_package_path=str(tmp / "slice2_pkg")
    )

    defn = registry.get_in_namespace("conn", "slice2_pkg")
    assert defn is not None, (
        "fixture must land in the registry before its scope can be checked"
    )
    assert isinstance(defn.source, ModuleSource), (
        "registrar must produce a ModuleSource — the lifetime assertion below "
        "only exists on that variant"
    )
    assert defn.source.lifetime is Lifetime.MODULE, (
        "ModuleSource must retain the declared tier"
    )
    assert defn.scope is FixtureScope.MODULE, (
        "Lifetime.MODULE must map to FixtureScope.MODULE — the caching machinery "
        "reads scope, so a hardcoded EACH here makes every module-lifetime "
        "fixture silently rebuild per test"
    )


def test_skips_undecorated_functions(tmp: TempDir) -> None:
    """Functions without the @oxi.fixture marker must not be registered."""
    registry = FixtureRegistry()
    (tmp / "slice1_pkg").mkdir()

    mod = types.ModuleType("slice1_pkg.__fixtures__")
    mod.__file__ = str(tmp / "slice1_pkg" / "__fixtures__.py")

    def not_a_fixture() -> object:
        return object()

    setattr(mod, "not_a_fixture", not_a_fixture)  # noqa: B010 — dynamic module attr

    register_module_source_fixtures(
        registry, mod, anchor_package_path=str(tmp / "slice1_pkg")
    )

    assert registry.get_in_namespace("not_a_fixture", "slice1_pkg") is None, (
        "functions without the __oxitest_fixture__ marker must be ignored"
    )


def test_collision_with_conftest_source_is_loud(tmp: TempDir) -> None:
    """Same (namespace, name) in conftest and module raises UsageError."""

    def _existing_conn() -> object:
        return object()

    registry = FixtureRegistry()
    registry.register(
        FixtureDef(
            name="conn",
            fixture_type=object,
            scope=FixtureScope.EACH,
            source=ConftestSource(
                func=_existing_conn,
                conftest_path=str(tmp / "slice1_pkg" / "conftest.py"),
            ),
            namespace="slice1_pkg",
        )
    )
    (tmp / "slice1_pkg").mkdir()
    mod = _make_fake_fixture_module(tmp)

    with raises(UsageError, match="declared twice"):
        register_module_source_fixtures(
            registry, mod, anchor_package_path=str(tmp / "slice1_pkg")
        )
