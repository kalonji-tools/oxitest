"""Tests for the plugin loading system."""

from __future__ import annotations

import sys
import types

from oxitest._bridge._raises import raises
from oxitest._bridge.plugin_loader import PluginLoadError, PluginRegistry, load_plugins
from oxitest.plugin import Plugin


def _install_fake_module(name: str, module: types.ModuleType) -> None:
    """Install a fake module into sys.modules for testing."""
    sys.modules[name] = module


def _remove_fake_module(name: str) -> None:
    """Remove a fake module from sys.modules."""
    sys.modules.pop(name, None)


def test_load_empty_plugins_returns_empty_registry():
    registry = load_plugins([], {})
    assert isinstance(registry, PluginRegistry), (
        f"expected PluginRegistry, got {type(registry).__name__}"
    )
    assert registry.entries == [], f"expected empty entries, got {registry.entries!r}"


def test_load_valid_plugin():
    mod = types.ModuleType("fake_plugin")
    mod.oxitest_plugin = lambda config=None: Plugin()  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _install_fake_module("fake_plugin", mod)
    try:
        registry = load_plugins(["fake_plugin"], {})
        assert len(registry.entries) == 1, (
            f"expected 1 entry, got {len(registry.entries)}"
        )
        actual = registry.entries[0].module_name
        assert actual == "fake_plugin", (
            f"expected module_name 'fake_plugin', got {actual!r}"
        )
    finally:
        _remove_fake_module("fake_plugin")


def test_load_plugin_receives_config():
    received: dict = {}

    def entry(config=None):
        received["config"] = config
        return Plugin()

    mod = types.ModuleType("cfg_plugin")
    mod.oxitest_plugin = entry  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _install_fake_module("cfg_plugin", mod)
    try:
        load_plugins(["cfg_plugin"], {"cfg_plugin": {"level": "DEBUG"}})
        assert received["config"] == {"level": "DEBUG"}, (
            f"expected config dict, got {received['config']!r}"
        )
    finally:
        _remove_fake_module("cfg_plugin")


def test_load_missing_module_raises():
    with raises(PluginLoadError, match="not found"):
        load_plugins(["nonexistent_oxitest_plugin_xyz"], {})


def test_load_no_entry_function_raises():
    mod = types.ModuleType("no_entry")
    _install_fake_module("no_entry", mod)
    try:
        with raises(PluginLoadError, match="has no oxitest_plugin"):
            load_plugins(["no_entry"], {})
    finally:
        _remove_fake_module("no_entry")


def test_load_wrong_return_type_raises():
    mod = types.ModuleType("bad_return")
    mod.oxitest_plugin = lambda config=None: "not a Plugin"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _install_fake_module("bad_return", mod)
    try:
        with raises(PluginLoadError, match="must return oxitest.Plugin"):
            load_plugins(["bad_return"], {})
    finally:
        _remove_fake_module("bad_return")


def test_load_entry_raises_wraps_error():
    def bad_entry(config=None):
        raise ValueError("boom")

    mod = types.ModuleType("raises_plugin")
    mod.oxitest_plugin = bad_entry  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _install_fake_module("raises_plugin", mod)
    try:
        with raises(PluginLoadError, match="raised"):
            load_plugins(["raises_plugin"], {})
    finally:
        _remove_fake_module("raises_plugin")


def test_registry_aggregates_across_plugins():
    class FakeBackend:
        def install(self):
            pass

        def uninstall(self):
            pass

        @property
        def records(self):
            return []

    mod1 = types.ModuleType("plug1")
    mod1.oxitest_plugin = lambda config=None: Plugin(log_backends=[FakeBackend()])  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    mod2 = types.ModuleType("plug2")
    mod2.oxitest_plugin = lambda config=None: Plugin(log_backends=[FakeBackend()])  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _install_fake_module("plug1", mod1)
    _install_fake_module("plug2", mod2)
    try:
        registry = load_plugins(["plug1", "plug2"], {})
        assert len(registry.log_backends) == 2, (
            f"expected 2 log backends, got {len(registry.log_backends)}"
        )
    finally:
        _remove_fake_module("plug1")
        _remove_fake_module("plug2")
