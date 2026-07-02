"""Tests for the plugin loading system."""

from __future__ import annotations

import types
from collections.abc import Callable

import oxitest
from oxitest import Fixture, helpers
from oxitest._bridge._errors import ConflictingDebuggerError
from oxitest._bridge._raises import raises
from oxitest._bridge.plugin_loader import PluginLoadError, PluginRegistry, load_plugins
from oxitest.plugin import Plugin


def test_load_empty_plugins_returns_empty_registry():
    registry = load_plugins([], {})
    assert isinstance(registry, PluginRegistry), (
        f"expected PluginRegistry, got {type(registry).__name__}"
    )
    assert registry.entries == [], f"expected empty entries, got {registry.entries!r}"


@oxitest.mark.inprocess
def test_load_valid_plugin(fake_module: Fixture[Callable]):
    mod = types.ModuleType("fake_plugin")
    mod.oxitest_plugin = lambda config=None: Plugin()  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    fake_module("fake_plugin", mod)
    registry = load_plugins(["fake_plugin"], {})
    assert len(registry.entries) == 1, f"expected 1 entry, got {len(registry.entries)}"
    actual = registry.entries[0].module_name
    assert actual == "fake_plugin", (
        f"expected module_name 'fake_plugin', got {actual!r}"
    )


@oxitest.mark.inprocess
def test_load_plugin_receives_config(fake_module: Fixture[Callable]):
    received: dict = {}

    def entry(config=None):
        received["config"] = config
        return Plugin()

    mod = types.ModuleType("cfg_plugin")
    mod.oxitest_plugin = entry  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    fake_module("cfg_plugin", mod)
    load_plugins(["cfg_plugin"], {"cfg_plugin": {"level": "DEBUG"}})
    assert received["config"] == {"level": "DEBUG"}, (
        f"expected config dict, got {received['config']!r}"
    )


def test_load_missing_module_raises():
    with raises(PluginLoadError, match="not found"):
        load_plugins(["nonexistent_oxitest_plugin_xyz"], {})


@oxitest.mark.inprocess
def test_load_no_entry_function_raises(fake_module: Fixture[Callable]):
    mod = types.ModuleType("no_entry")
    fake_module("no_entry", mod)
    with raises(PluginLoadError, match="has no oxitest_plugin"):
        load_plugins(["no_entry"], {})


@oxitest.mark.inprocess
def test_load_wrong_return_type_raises(fake_module: Fixture[Callable]):
    mod = types.ModuleType("bad_return")
    mod.oxitest_plugin = lambda config=None: "not a Plugin"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    fake_module("bad_return", mod)
    with raises(PluginLoadError, match="must return oxitest.Plugin"):
        load_plugins(["bad_return"], {})


@oxitest.mark.inprocess
def test_load_entry_raises_wraps_error(fake_module: Fixture[Callable]):
    def bad_entry(config=None):
        raise ValueError("boom")

    mod = types.ModuleType("raises_plugin")
    mod.oxitest_plugin = bad_entry  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    fake_module("raises_plugin", mod)
    with raises(PluginLoadError, match="raised"):
        load_plugins(["raises_plugin"], {})


@oxitest.mark.inprocess
def test_registry_aggregates_across_plugins(fake_module: Fixture[Callable]):
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
    fake_module("plug1", mod1)
    fake_module("plug2", mod2)
    registry = load_plugins(["plug1", "plug2"], {})
    assert len(registry.log_backends) == 2, (
        f"expected 2 log backends, got {len(registry.log_backends)}"
    )


@oxitest.mark.inprocess
def test_conflicting_debugger_backends_raises(fake_module: Fixture[Callable]):
    """Two plugins providing debugger backends should raise ConflictingDebuggerError."""
    mod_a = types.ModuleType("dbg_plugin_a")
    mod_a.oxitest_plugin = lambda config=None: Plugin(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        debugger_backend=helpers.common.RecordingDebugger()
    )
    mod_b = types.ModuleType("dbg_plugin_b")
    mod_b.oxitest_plugin = lambda config=None: Plugin(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        debugger_backend=helpers.common.RecordingDebugger()
    )
    fake_module("dbg_plugin_a", mod_a)
    fake_module("dbg_plugin_b", mod_b)
    with raises(ConflictingDebuggerError) as exc_info:
        load_plugins(["dbg_plugin_a", "dbg_plugin_b"], {})
    assert "dbg_plugin_a" in str(exc_info.value), (
        f"error should name first plugin: {exc_info.value}"
    )
    assert "dbg_plugin_b" in str(exc_info.value), (
        f"error should name second plugin: {exc_info.value}"
    )


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
def test_single_debugger_backend_is_valid(fake_module: Fixture[Callable]):
    """One plugin providing a debugger backend should not raise."""
    mod = types.ModuleType("solo_dbg")
    mod.oxitest_plugin = lambda config=None: Plugin(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        debugger_backend=helpers.common.RecordingDebugger()
    )
    fake_module("solo_dbg", mod)
    registry = load_plugins(["solo_dbg"], {})
    assert len(registry.debugger_backends) == 1, (
        f"expected 1 debugger backend, got {len(registry.debugger_backends)}"
    )


def test_fixture_provider_scope_default():
    """FixtureProvider without scope property defaults to 'each'."""

    class MinimalProvider:
        @property
        def name(self) -> str:
            return "test"

        @property
        def fixture_type(self) -> type:
            return int

        def create(self, ctx):
            return 42

        def teardown(self, value):
            pass

    provider = MinimalProvider()
    assert getattr(provider, "scope", "each") == "each", (
        "provider without scope should default to 'each'"
    )


def test_fixture_provider_scope_custom():
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

        def create(self, ctx):
            return 42

        def teardown(self, value):
            pass

    provider = SessionProvider()
    assert getattr(provider, "scope", "each") == "session", (
        "provider with scope='session' should return 'session'"
    )
    assert getattr(provider, "autouse", False) is True, (
        "provider with autouse=True should return True"
    )
