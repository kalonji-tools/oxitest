"""Tests for the plugin loading system."""

from __future__ import annotations

import types
from typing import Any, Never

import oxitest
from oxitest import TestContext, helpers, raises
from oxitest._bridge._errors import ConflictingDebuggerError, OxitestError
from oxitest._bridge.plugin_loader import (
    PluginLoadError,
    PluginRegistry,
    activate_deferred_plugins,
    load_plugins,
)
from oxitest.plugin import Plugin


def test_load_empty_plugins_returns_empty_registry() -> None:
    """load_plugins([]) returns an empty PluginRegistry with no entries."""
    registry = load_plugins([], {})
    assert isinstance(registry, PluginRegistry), (
        f"expected PluginRegistry, got {type(registry).__name__}"
    )
    assert registry.entries == (), f"expected empty entries, got {registry.entries!r}"


@oxitest.mark.inprocess
def test_load_valid_plugin(ctx: TestContext) -> None:
    """A module with a valid oxitest_plugin() entry function is loaded."""
    mod = helpers.common.make_plugin_module("fake_plugin", lambda **_: Plugin())
    helpers.common.install_module(ctx, "fake_plugin", mod)

    registry = load_plugins(["fake_plugin"], {})
    assert len(registry.entries) == 1, f"expected 1 entry, got {len(registry.entries)}"
    actual = registry.entries[0].module_name
    assert actual == "fake_plugin", (
        f"expected module_name 'fake_plugin', got {actual!r}"
    )


@oxitest.mark.inprocess
def test_load_plugin_receives_config(ctx: TestContext) -> None:
    """Plugin config from pyproject.toml is forwarded to oxitest_plugin()."""
    received: dict = {}

    def entry(config: dict[str, object] | None = None) -> Plugin:
        received["config"] = config
        return Plugin()

    mod = helpers.common.make_plugin_module("cfg_plugin", entry)
    helpers.common.install_module(ctx, "cfg_plugin", mod)

    load_plugins(["cfg_plugin"], {"cfg_plugin": {"level": "DEBUG"}})
    assert received["config"] == {"level": "DEBUG"}, (
        f"expected config dict, got {received['config']!r}"
    )


def test_load_missing_module_raises() -> None:
    """load_plugins raises PluginLoadError when a named module cannot be imported."""
    with raises(PluginLoadError, match="not found"):
        load_plugins(["nonexistent_oxitest_plugin_xyz"], {})


@oxitest.mark.inprocess
def test_load_no_entry_function_raises(ctx: TestContext) -> None:
    """load_plugins raises PluginLoadError when the module has no oxitest_plugin."""
    mod = types.ModuleType("no_entry")
    helpers.common.install_module(ctx, "no_entry", mod)

    with raises(PluginLoadError, match="has no oxitest_plugin"):
        load_plugins(["no_entry"], {})


@oxitest.mark.inprocess
def test_load_wrong_return_type_raises(ctx: TestContext) -> None:
    """load_plugins raises PluginLoadError when oxitest_plugin() returns non-Plugin."""
    mod = helpers.common.make_plugin_module("bad_return", lambda **_: "not a Plugin")
    helpers.common.install_module(ctx, "bad_return", mod)

    with raises(PluginLoadError, match="must return oxitest.Plugin"):
        load_plugins(["bad_return"], {})


@oxitest.mark.inprocess
def test_load_entry_raises_wraps_error(ctx: TestContext) -> None:
    """Exceptions from oxitest_plugin() are wrapped in PluginLoadError with context."""

    def bad_entry(**_: object) -> Never:
        msg = "boom"
        raise ValueError(msg)

    mod = helpers.common.make_plugin_module("raises_plugin", bad_entry)
    helpers.common.install_module(ctx, "raises_plugin", mod)

    with raises(PluginLoadError, match="raised"):
        load_plugins(["raises_plugin"], {})


@oxitest.mark.inprocess
def test_registry_aggregates_across_plugins(ctx: TestContext) -> None:
    """PluginRegistry collects contributions (e.g. log_backends) from all plugins."""

    class FakeBackend:
        def install(self) -> None:
            pass

        def uninstall(self) -> None:
            pass

        @property
        def records(self) -> list[object]:
            return []

    mod1 = helpers.common.make_plugin_module(
        "plug1",
        lambda **_: Plugin(log_backends=(FakeBackend(),)),
    )
    mod2 = helpers.common.make_plugin_module(
        "plug2",
        lambda **_: Plugin(log_backends=(FakeBackend(),)),
    )
    helpers.common.install_module(ctx, "plug1", mod1)
    helpers.common.install_module(ctx, "plug2", mod2)

    registry = load_plugins(["plug1", "plug2"], {})
    assert len(registry.log_backends) == 2, (
        f"expected 2 log backends, got {len(registry.log_backends)}"
    )


@oxitest.mark.inprocess
def test_conflicting_debugger_backends_raises(ctx: TestContext) -> None:
    """Two plugins providing debugger backends should raise ConflictingDebuggerError."""
    mod_a = helpers.common.make_plugin_module(
        "dbg_plugin_a",
        lambda **_: Plugin(debugger_backend=helpers.common.RecordingDebugger()),
    )
    mod_b = helpers.common.make_plugin_module(
        "dbg_plugin_b",
        lambda **_: Plugin(debugger_backend=helpers.common.RecordingDebugger()),
    )
    helpers.common.install_module(ctx, "dbg_plugin_a", mod_a)
    helpers.common.install_module(ctx, "dbg_plugin_b", mod_b)

    with raises(ConflictingDebuggerError) as exc_info:
        load_plugins(["dbg_plugin_a", "dbg_plugin_b"], {})
    assert "dbg_plugin_a" in str(exc_info.value), (
        f"error should name first plugin: {exc_info.value}"
    )
    assert "dbg_plugin_b" in str(exc_info.value), (
        f"error should name second plugin: {exc_info.value}"
    )


def test_flatten_protocol_returns_empty_for_no_plugins() -> None:
    """An empty PluginRegistry returns empty tuples for all extension points."""
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
def test_single_debugger_backend_is_valid(ctx: TestContext) -> None:
    """One plugin providing a debugger backend should not raise."""
    mod = helpers.common.make_plugin_module(
        "solo_dbg",
        lambda **_: Plugin(debugger_backend=helpers.common.RecordingDebugger()),
    )
    helpers.common.install_module(ctx, "solo_dbg", mod)

    registry = load_plugins(["solo_dbg"], {})
    assert registry.debugger_backend is not None, (
        "expected a debugger backend, got None"
    )


def test_fixture_provider_scope_default() -> None:
    """FixtureProvider without scope property defaults to 'each'."""

    class MinimalProvider:
        @property
        def name(self) -> str:
            return "test"

        @property
        def fixture_type(self) -> type:
            return int

        def create(self, **_: Any) -> int:
            return 42

        def teardown(self, **_: Any) -> None:
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

        def create(self, **_: Any) -> int:
            return 42

        def teardown(self, **_: Any) -> None:
            pass

    provider = SessionProvider()
    assert getattr(provider, "scope", "each") == "session", (
        "provider with scope='session' should return 'session'"
    )
    assert getattr(provider, "autouse", False) is True, (
        "provider with autouse=True should return True"
    )


def test_activate_deferred_plugins_rejects_invalid_settings_json() -> None:
    """activate_deferred_plugins raises OxitestError on malformed settings JSON."""
    registry = PluginRegistry()
    with raises(OxitestError, match="invalid plugin settings JSON"):
        activate_deferred_plugins(registry, "not valid json", "{}")


def test_activate_deferred_plugins_rejects_invalid_cli_json() -> None:
    """activate_deferred_plugins raises OxitestError on malformed CLI values JSON."""
    registry = PluginRegistry()
    with raises(OxitestError, match="invalid CLI values JSON"):
        activate_deferred_plugins(registry, "{}", "not valid json")
