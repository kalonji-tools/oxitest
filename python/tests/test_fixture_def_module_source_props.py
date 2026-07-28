"""Tests for FixtureDef .func and .conftest_path with ModuleSource backing.

Task 5 of the fixture-redesign slice-1 plan. .conftest_path branch was
added defensively in Task 4 (see 9059aa59); this file provides the test
coverage the Task-4 review flagged as missing, plus covers Task 5's own
.func branch addition.
"""

from __future__ import annotations

from oxitest._bridge._fixture_registry import (
    FixtureDef,
    FixtureScope,
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


def test_conftest_path_property_returns_defining_module_path() -> None:
    """FixtureDef.conftest_path returns module path for ModuleSource."""
    defn = _make_module_backed_def()
    assert defn.conftest_path == "/pkg/__fixtures__.py", (
        "conftest_path backward-compat property must return the fixture's "
        "module path — diagnostics rely on this to locate the declaration"
    )
