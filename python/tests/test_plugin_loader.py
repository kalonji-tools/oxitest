"""Tests for the plugin loading system."""

from __future__ import annotations

import sys
import types

import oxitest
from conftest import helpers
from oxitest._bridge._errors import ConflictingDebuggerError
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


@oxitest.mark.inprocess
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


@oxitest.mark.inprocess
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


@oxitest.mark.inprocess
def test_load_no_entry_function_raises():
    mod = types.ModuleType("no_entry")
    _install_fake_module("no_entry", mod)
    try:
        with raises(PluginLoadError, match="has no oxitest_plugin"):
            load_plugins(["no_entry"], {})
    finally:
        _remove_fake_module("no_entry")


@oxitest.mark.inprocess
def test_load_wrong_return_type_raises():
    mod = types.ModuleType("bad_return")
    mod.oxitest_plugin = lambda config=None: "not a Plugin"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _install_fake_module("bad_return", mod)
    try:
        with raises(PluginLoadError, match="must return oxitest.Plugin"):
            load_plugins(["bad_return"], {})
    finally:
        _remove_fake_module("bad_return")


@oxitest.mark.inprocess
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


@oxitest.mark.inprocess
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
    mod1.oxitest_plugin = lambda config=None: Plugin(log_backends=(FakeBackend(),))  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    mod2 = types.ModuleType("plug2")
    mod2.oxitest_plugin = lambda config=None: Plugin(log_backends=(FakeBackend(),))  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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


@oxitest.mark.inprocess
def test_conflicting_debugger_backends_raises():
    """Two plugins providing debugger backends should raise ConflictingDebuggerError."""
    mod_a = types.ModuleType("dbg_plugin_a")
    mod_a.oxitest_plugin = lambda config=None: Plugin(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        debugger_backend=helpers.common.RecordingDebugger()
    )
    mod_b = types.ModuleType("dbg_plugin_b")
    mod_b.oxitest_plugin = lambda config=None: Plugin(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        debugger_backend=helpers.common.RecordingDebugger()
    )
    _install_fake_module("dbg_plugin_a", mod_a)
    _install_fake_module("dbg_plugin_b", mod_b)
    try:
        with raises(ConflictingDebuggerError) as exc_info:
            load_plugins(["dbg_plugin_a", "dbg_plugin_b"], {})
        assert "dbg_plugin_a" in str(exc_info.value), (
            f"error should name first plugin: {exc_info.value}"
        )
        assert "dbg_plugin_b" in str(exc_info.value), (
            f"error should name second plugin: {exc_info.value}"
        )
    finally:
        _remove_fake_module("dbg_plugin_a")
        _remove_fake_module("dbg_plugin_b")


def test_flatten_protocol_returns_empty_for_no_plugins():
    registry = PluginRegistry()
    assert registry.log_backends == (), f"expected empty, got {registry.log_backends!r}"
    assert registry.fixture_providers == (), (
        f"expected empty, got {registry.fixture_providers!r}"
    )
    assert registry.execution_wrappers == (), (
        f"expected empty, got {registry.execution_wrappers!r}"
    )
    assert registry.collectors == (), f"expected empty, got {registry.collectors!r}"
    assert registry.reporters == (), f"expected empty, got {registry.reporters!r}"


@oxitest.mark.inprocess
def test_single_debugger_backend_is_valid():
    """One plugin providing a debugger backend should not raise."""
    mod = types.ModuleType("solo_dbg")
    mod.oxitest_plugin = lambda config=None: Plugin(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        debugger_backend=helpers.common.RecordingDebugger()
    )
    _install_fake_module("solo_dbg", mod)
    try:
        registry = load_plugins(["solo_dbg"], {})
        assert len(registry.debugger_backends) == 1, (
            f"expected 1 debugger backend, got {len(registry.debugger_backends)}"
        )
    finally:
        _remove_fake_module("solo_dbg")
