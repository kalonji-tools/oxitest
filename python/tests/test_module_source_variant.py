"""Tests for ModuleSource fixture source variant (ADR-0009, slice 1)."""

from __future__ import annotations

from oxitest._bridge._fixture_registry import (
    BuiltinSource,
    ConftestSource,
    FixtureSource,
    ModuleSource,
    PluginSource,
)
from oxitest._bridge._lifetime import Lifetime


def test_module_source_carries_all_four_fields() -> None:
    """ModuleSource preserves all four fields (func, paths, lifetime) for later use."""
    src = ModuleSource(
        func=str,
        defining_module_path="/a/pkg/__fixtures__.py",
        anchor_package_path="/a/pkg",
        lifetime=Lifetime.FUNCTION,
    )
    assert src.func is str, "func retained"
    assert src.defining_module_path == "/a/pkg/__fixtures__.py", (
        "defining_module_path preserved verbatim for diagnostics"
    )
    assert src.anchor_package_path == "/a/pkg", (
        "anchor_package_path preserved verbatim for B1 boundary calc (slice 6)"
    )
    assert src.lifetime is Lifetime.FUNCTION, "lifetime tier stored as enum"


def test_fixture_source_union_includes_module_source() -> None:
    """FixtureSource union includes ModuleSource as a valid source variant."""
    # Static: this test compiles = ModuleSource is a member of the union.
    src: FixtureSource
    src = ModuleSource(
        func=str,
        defining_module_path="",
        anchor_package_path="",
        lifetime=Lifetime.FUNCTION,
    )
    src = ConftestSource(func=str, conftest_path="")
    src = BuiltinSource(impl_cls=type)
    src = PluginSource(provider=None, plugin_module="")
    assert src is not None, "sanity — union type accepts all four variants"
