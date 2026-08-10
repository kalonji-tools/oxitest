"""Tests for FixtureDef .func and .declaration_path with ModuleSource backing.

Task 5 of the fixture-redesign slice-1 plan. .declaration_path branch was
added defensively in Task 4 (see 9059aa59); this file provides the test
coverage the Task-4 review flagged as missing, plus covers Task 5's own
.func branch addition.
"""

from __future__ import annotations

from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureScope,
    FrameworkSource,
    ModuleSource,
)
from oxitest._bridge._lifetime import Lifetime


def _make_module_backed_def(name: str = "conn") -> FixtureDef:
    def _conn() -> object:
        return object()

    return FixtureDef(
        name=name,
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=ModuleSource(
            func=_conn,
            defining_module_path="/pkg/__fixtures__.py",
            anchor_package_path="/pkg",
            lifetime=Lifetime.FUNCTION,
        ),
        namespace="pkg",
    )


def test_func_property_returns_module_source_func() -> None:
    """FixtureDef.func returns the wrapped callable for ModuleSource."""
    defn = _make_module_backed_def()
    fn = defn.func
    result = fn()
    assert isinstance(result, object), (
        ".func on a ModuleSource-backed FixtureDef must return the wrapped "
        "callable so the existing instantiator machinery can invoke it"
    )


def test_declaration_path_property_returns_defining_module_path() -> None:
    """FixtureDef.declaration_path returns module path for ModuleSource."""
    defn = _make_module_backed_def()
    assert defn.declaration_path == "/pkg/__fixtures__.py", (
        "declaration_path backward-compat property must return the fixture's "
        "module path — diagnostics rely on this to locate the declaration"
    )


def test_package_fixture_is_visible_from_a_descendant_package() -> None:
    """The slice-5 stopgap answered True for every package fixture, everywhere."""
    # Arrange
    defn = FixtureDef(
        name="api_conn",
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

    # Act
    from_below = defn.is_visible_from("/t/api/v1/test_v1.py")
    from_sibling = defn.is_visible_from("/t/admin/test_admin.py")

    # Assert
    assert from_below is True, (
        "a descendant package must reach its ancestor's fixtures or B1 is a "
        "per-directory silo rather than a containment rule"
    )
    assert from_sibling is False, (
        "a sibling package reaching in is the exact leak this slice closes — "
        "before it, every package fixture was visible run-wide"
    )


def test_module_source_exposes_its_anchor_and_other_sources_do_not() -> None:
    """`anchor` is the one query the registry's ordering rule needs."""
    # Arrange
    module_defn = FixtureDef(
        name="api_conn",
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
    conftest_defn = FixtureDef(
        name="legacy",
        fixture_type=object,
        scope=FixtureScope.EACH,
        source=FrameworkSource(func=lambda: None, origin="/t/conftest.py"),
    )

    # Act
    module_anchor = module_defn.anchor
    conftest_anchor = conftest_defn.anchor

    # Assert
    assert module_anchor == "/t/api", (
        "the registry sorts candidates by anchor depth; reading through to "
        "`source.anchor_package_path` at every call site would spread the "
        "ModuleSource isinstance check across four modules"
    )
    assert conftest_anchor is None, (
        "conftest, plugin and builtin fixtures are exempt from B1 (ADR-0009 "
        "Rules 6 and 7), so None is what tells the ordering rule to leave "
        "their locality semantics alone"
    )
