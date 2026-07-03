"""Tests for lazy plugin module import."""

from __future__ import annotations

import sys
import types

import oxitest
from oxitest._bridge.plugin_loader import (
    EAGER_PROTOCOLS,
    LAZY_PROTOCOLS,
    PluginEntry,
    PluginRegistry,
    load_plugins,
)
from oxitest.plugin import Plugin


def test_plugin_with_eager_protocol_imported_immediately():
    assert PluginEntry.needs_eager_import(["reporter"]), "reporter is an eager protocol"
    assert PluginEntry.needs_eager_import(["collector"]), (
        "collector is an eager protocol"
    )
    assert PluginEntry.needs_eager_import(["async_backend"]), (
        "async_backend is an eager protocol"
    )
    assert PluginEntry.needs_eager_import(["coverage_provider"]), (
        "coverage_provider is an eager protocol"
    )


def test_plugin_without_protocol_declaration_imported_eagerly():
    assert PluginEntry.needs_eager_import(None), (
        "no declaration means import eagerly (safe default)"
    )
    assert PluginEntry.needs_eager_import([]), (
        "empty declaration means import eagerly (safe default)"
    )


def test_plugin_with_only_lazy_protocols_not_eager():
    assert not PluginEntry.needs_eager_import(["fixture_provider"]), (
        "fixture_provider is a lazy protocol"
    )
    assert not PluginEntry.needs_eager_import(["log_backend"]), (
        "log_backend is a lazy protocol"
    )
    assert not PluginEntry.needs_eager_import(["execution_wrapper"]), (
        "execution_wrapper is a lazy protocol"
    )
    assert not PluginEntry.needs_eager_import(["debugger_backend"]), (
        "debugger_backend is a lazy protocol"
    )
    assert not PluginEntry.needs_eager_import(["fixture_provider", "log_backend"]), (
        "all-lazy combination should not be eager"
    )


def test_mixed_protocols_imported_eagerly():
    assert PluginEntry.needs_eager_import(["fixture_provider", "reporter"]), (
        "presence of one eager protocol forces eager import"
    )


def test_eager_protocols_constant_contains_expected_values():
    expected = frozenset(
        {"reporter", "collector", "async_backend", "coverage_provider"}
    )
    assert expected == EAGER_PROTOCOLS, (
        f"expected EAGER_PROTOCOLS == {expected!r}, got {EAGER_PROTOCOLS!r}"
    )


def test_lazy_protocols_constant_contains_expected_values():
    expected = frozenset(
        {
            "log_backend",
            "fixture_provider",
            "helper_provider",
            "execution_wrapper",
            "debugger_backend",
        }
    )
    assert expected == LAZY_PROTOCOLS, (
        f"expected LAZY_PROTOCOLS == {expected!r}, got {LAZY_PROTOCOLS!r}"
    )


def test_deferred_classmethod_creates_unloaded_entry():
    entry = PluginEntry.deferred("some.plugin", ["fixture_provider"])

    assert entry.module_name == "some.plugin", (
        f"expected module_name 'some.plugin', got {entry.module_name!r}"
    )
    assert entry.plugin is None, f"expected plugin=None, got {entry.plugin!r}"
    assert entry.is_loaded is False, (
        f"expected is_loaded=False, got {entry.is_loaded!r}"
    )
    assert entry.declared_protocols == ["fixture_provider"], (
        "expected declared_protocols=['fixture_provider'], "
        f"got {entry.declared_protocols!r}"
    )


def test_plugin_entry_default_is_loaded():
    plugin = Plugin()
    entry = PluginEntry(module_name="some.plugin", plugin=plugin)

    assert entry.is_loaded is True, (
        f"default PluginEntry should be loaded, got is_loaded={entry.is_loaded!r}"
    )
    assert entry.declared_protocols is None, (
        f"default declared_protocols should be None, got {entry.declared_protocols!r}"
    )


@oxitest.mark.inprocess
def test_deferred_entry_ensure_loaded_imports_module():
    mod = types.ModuleType("lazy_fixture_plugin")
    setattr(mod, "oxitest_plugin", Plugin)
    sys.modules["lazy_fixture_plugin"] = mod
    try:
        entry = PluginEntry.deferred("lazy_fixture_plugin", ["fixture_provider"])
        assert entry.is_loaded is False, (
            "deferred entry should not be loaded yet, "
            f"got is_loaded={entry.is_loaded!r}"
        )

        result = entry.ensure_loaded()

        assert isinstance(result, Plugin), (
            f"ensure_loaded() should return Plugin, got {type(result).__name__}"
        )
        assert entry.is_loaded is True, (
            "entry should be marked loaded after ensure_loaded(), "
            f"got {entry.is_loaded!r}"
        )
        assert entry.plugin is result, (
            "entry.plugin should be the same object returned by ensure_loaded()"
        )
    finally:
        sys.modules.pop("lazy_fixture_plugin", None)


def test_ensure_loaded_on_already_loaded_entry_returns_plugin():
    plugin = Plugin()
    entry = PluginEntry(module_name="some.plugin", plugin=plugin)

    result = entry.ensure_loaded()

    assert result is plugin, (
        "ensure_loaded() on an already-loaded entry should return the existing plugin"
    )


@oxitest.mark.inprocess
def test_load_plugins_defers_lazy_only_plugin():
    mod = types.ModuleType("lazy_only_plugin")
    setattr(mod, "oxitest_plugin", Plugin)
    sys.modules["lazy_only_plugin"] = mod
    try:
        registry = load_plugins(
            ["lazy_only_plugin"],
            {"lazy_only_plugin": {"protocols": ["fixture_provider"]}},
        )
        assert len(registry.entries) == 1, (
            f"expected 1 entry, got {len(registry.entries)}"
        )
        entry = registry.entries[0]
        assert entry.module_name == "lazy_only_plugin", (
            f"expected module_name 'lazy_only_plugin', got {entry.module_name!r}"
        )
        assert entry.is_loaded is False, (
            f"lazy-only plugin should be deferred, got is_loaded={entry.is_loaded!r}"
        )
        assert entry.plugin is None, (
            f"deferred plugin should be None, got {entry.plugin!r}"
        )
    finally:
        sys.modules.pop("lazy_only_plugin", None)


@oxitest.mark.inprocess
def test_load_plugins_eager_imports_plugin_with_eager_protocol():
    mod = types.ModuleType("eager_reporter_plugin")
    setattr(mod, "oxitest_plugin", lambda config=None: Plugin())
    sys.modules["eager_reporter_plugin"] = mod
    try:
        registry = load_plugins(
            ["eager_reporter_plugin"],
            {"eager_reporter_plugin": {"protocols": ["reporter"]}},
        )
        assert len(registry.entries) == 1, (
            f"expected 1 entry, got {len(registry.entries)}"
        )
        entry = registry.entries[0]
        assert entry.is_loaded is True, (
            "reporter plugin should be eagerly loaded, "
            f"got is_loaded={entry.is_loaded!r}"
        )
        assert entry.plugin is not None, "eagerly loaded plugin should not be None"
    finally:
        sys.modules.pop("eager_reporter_plugin", None)


@oxitest.mark.inprocess
def test_load_plugins_eager_imports_plugin_with_no_protocols_declared():
    mod = types.ModuleType("no_protocols_plugin")
    setattr(mod, "oxitest_plugin", lambda config=None: Plugin())
    sys.modules["no_protocols_plugin"] = mod
    try:
        registry = load_plugins(["no_protocols_plugin"], {})
        assert len(registry.entries) == 1, (
            f"expected 1 entry, got {len(registry.entries)}"
        )
        entry = registry.entries[0]
        assert entry.is_loaded is True, (
            f"plugin without protocol declaration should be eagerly loaded, "
            f"got is_loaded={entry.is_loaded!r}"
        )
        assert entry.plugin is not None, "eagerly loaded plugin should not be None"
    finally:
        sys.modules.pop("no_protocols_plugin", None)


@oxitest.mark.inprocess
def test_registry_register_deferred_appends_entry():
    registry = PluginRegistry()
    entry = PluginEntry.deferred("deferred.plugin", ["log_backend"])

    registry.register_deferred(entry)

    assert len(registry.entries) == 1, (
        f"expected 1 entry after register_deferred, got {len(registry.entries)}"
    )
    assert registry.entries[0] is entry, "registered entry should be the same object"


@oxitest.mark.inprocess
def test_registry_resolve_fixture_providers_loads_deferred_fixture_plugin():
    mod = types.ModuleType("deferred_fixture_plugin")

    class FakeToken:
        """Marker type for FakeFixtureProvider."""

    class FakeFixtureProvider:
        @property
        def name(self) -> str:
            return "fake"

        @property
        def fixture_type(self) -> type:
            return FakeToken

        def create(self, ctx: object) -> FakeToken:
            return FakeToken()

        def teardown(self, value: object) -> None:
            pass

        @property
        def scope(self) -> str:
            return "each"

        @property
        def autouse(self) -> bool:
            return False

    setattr(
        mod,
        "oxitest_plugin",
        lambda: Plugin(fixture_providers=(FakeFixtureProvider(),)),
    )
    sys.modules["deferred_fixture_plugin"] = mod
    try:
        registry = PluginRegistry()
        entry = PluginEntry.deferred("deferred_fixture_plugin", ["fixture_provider"])
        registry.register_deferred(entry)

        providers = registry.resolve_fixture_providers()

        assert len(providers) == 1, f"expected 1 fixture provider, got {len(providers)}"
        assert isinstance(providers[0], FakeFixtureProvider), (
            f"expected FakeFixtureProvider, got {type(providers[0]).__name__}"
        )
        assert entry.is_loaded is True, (
            f"deferred fixture plugin should be loaded after resolve, "
            f"got is_loaded={entry.is_loaded!r}"
        )
    finally:
        sys.modules.pop("deferred_fixture_plugin", None)


@oxitest.mark.inprocess
def test_registry_resolve_fixture_providers_skips_non_fixture_deferred():
    registry = PluginRegistry()
    entry = PluginEntry.deferred("lazy_log_plugin", ["log_backend"])
    registry.register_deferred(entry)

    providers = registry.resolve_fixture_providers()

    assert providers == [], (
        f"non-fixture deferred plugin should yield no providers, got {providers!r}"
    )
    assert entry.is_loaded is False, (
        "non-fixture deferred plugin should not be loaded, "
        f"got is_loaded={entry.is_loaded!r}"
    )
