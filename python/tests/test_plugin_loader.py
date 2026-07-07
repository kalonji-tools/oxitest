"""Tests for the plugin loading system."""

from __future__ import annotations

import sys
import types
from typing import Never

import oxitest
from oxitest import helpers, raises
from oxitest._bridge._errors import ConflictingDebuggerError
from oxitest._bridge.plugin_loader import PluginLoadError, PluginRegistry, load_plugins
from oxitest.plugin import Plugin


def _install_fake_module(name: str, module: types.ModuleType) -> None:
    """Install a fake module into sys.modules for testing."""
    sys.modules[name] = module


def _remove_fake_module(name: str) -> None:
    """Remove a fake module from sys.modules."""
    sys.modules.pop(name, None)


def test_load_empty_plugins_returns_empty_registry() -> None:
    registry = load_plugins([], {})
    assert isinstance(registry, PluginRegistry), (
        f"expected PluginRegistry, got {type(registry).__name__}"
    )
    assert registry.entries == [], f"expected empty entries, got {registry.entries!r}"


@oxitest.mark.inprocess
def test_load_valid_plugin() -> None:
    mod = types.ModuleType("fake_plugin")
    setattr(mod, "oxitest_plugin", lambda **_: Plugin())
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
def test_load_plugin_receives_config() -> None:
    received: dict = {}

    def entry(config: dict[str, object] | None = None) -> Plugin:
        received["config"] = config
        return Plugin()

    mod = types.ModuleType("cfg_plugin")
    setattr(mod, "oxitest_plugin", entry)
    _install_fake_module("cfg_plugin", mod)
    try:
        load_plugins(["cfg_plugin"], {"cfg_plugin": {"level": "DEBUG"}})
        assert received["config"] == {"level": "DEBUG"}, (
            f"expected config dict, got {received['config']!r}"
        )
    finally:
        _remove_fake_module("cfg_plugin")


def test_load_missing_module_raises() -> None:
    with raises(PluginLoadError, match="not found"):
        load_plugins(["nonexistent_oxitest_plugin_xyz"], {})


@oxitest.mark.inprocess
def test_load_no_entry_function_raises() -> None:
    mod = types.ModuleType("no_entry")
    _install_fake_module("no_entry", mod)
    try:
        with raises(PluginLoadError, match="has no oxitest_plugin"):
            load_plugins(["no_entry"], {})
    finally:
        _remove_fake_module("no_entry")


@oxitest.mark.inprocess
def test_load_wrong_return_type_raises() -> None:
    mod = types.ModuleType("bad_return")
    setattr(mod, "oxitest_plugin", lambda **_: "not a Plugin")
    _install_fake_module("bad_return", mod)
    try:
        with raises(PluginLoadError, match="must return oxitest.Plugin"):
            load_plugins(["bad_return"], {})
    finally:
        _remove_fake_module("bad_return")


@oxitest.mark.inprocess
def test_load_entry_raises_wraps_error() -> None:
    def bad_entry(**_: object) -> Never:
        msg = "boom"
        raise ValueError(msg)

    mod = types.ModuleType("raises_plugin")
    setattr(mod, "oxitest_plugin", bad_entry)
    _install_fake_module("raises_plugin", mod)
    try:
        with raises(PluginLoadError, match="raised"):
            load_plugins(["raises_plugin"], {})
    finally:
        _remove_fake_module("raises_plugin")


@oxitest.mark.inprocess
def test_registry_aggregates_across_plugins() -> None:
    class FakeBackend:
        def install(self) -> None:
            pass

        def uninstall(self) -> None:
            pass

        @property
        def records(self) -> list[object]:
            return []

    mod1 = types.ModuleType("plug1")
    setattr(
        mod1,
        "oxitest_plugin",
        lambda **_: Plugin(log_backends=(FakeBackend(),)),
    )
    mod2 = types.ModuleType("plug2")
    setattr(
        mod2,
        "oxitest_plugin",
        lambda **_: Plugin(log_backends=(FakeBackend(),)),
    )
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
def test_conflicting_debugger_backends_raises() -> None:
    """Two plugins providing debugger backends should raise ConflictingDebuggerError."""
    mod_a = types.ModuleType("dbg_plugin_a")
    setattr(
        mod_a,
        "oxitest_plugin",
        lambda **_: Plugin(debugger_backend=helpers.common.RecordingDebugger()),
    )
    mod_b = types.ModuleType("dbg_plugin_b")
    setattr(
        mod_b,
        "oxitest_plugin",
        lambda **_: Plugin(debugger_backend=helpers.common.RecordingDebugger()),
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


def test_flatten_protocol_returns_empty_for_no_plugins() -> None:
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
def test_single_debugger_backend_is_valid() -> None:
    """One plugin providing a debugger backend should not raise."""
    mod = types.ModuleType("solo_dbg")
    setattr(
        mod,
        "oxitest_plugin",
        lambda **_: Plugin(debugger_backend=helpers.common.RecordingDebugger()),
    )
    _install_fake_module("solo_dbg", mod)
    try:
        registry = load_plugins(["solo_dbg"], {})
        assert len(registry.debugger_backends) == 1, (
            f"expected 1 debugger backend, got {len(registry.debugger_backends)}"
        )
    finally:
        _remove_fake_module("solo_dbg")


def test_fixture_provider_scope_default() -> None:
    """FixtureProvider without scope property defaults to 'each'."""

    class MinimalProvider:
        @property
        def name(self) -> str:
            return "test"

        @property
        def fixture_type(self) -> type:
            return int

        def create(self, ctx: object) -> int:
            return 42

        def teardown(self, value: object) -> None:
            pass

    provider = MinimalProvider()
    assert getattr(provider, "scope", "each") == "each", (
        "provider without scope should default to 'each'"
    )


def test_fixture_provider_scope_custom() -> None:
    """FixtureProvider with scope property is respected."""

    class SessionProvider:
        @property
        def name(self) -> str:
            return "test"

        @property
        def fixture_type(self) -> type:
            return int

        @property
        def scope(self) -> str:
            return "session"

        @property
        def autouse(self) -> bool:
            return True

        def create(self, ctx: object) -> int:
            return 42

        def teardown(self, value: object) -> None:
            pass

    provider = SessionProvider()
    assert getattr(provider, "scope", "each") == "session", (
        "provider with scope='session' should return 'session'"
    )
    assert getattr(provider, "autouse", False) is True, (
        "provider with autouse=True should return True"
    )
