"""Tests for lazy plugin module import."""

from __future__ import annotations

import sys
import types
from typing import Any, Never

import oxitest
from oxitest import helpers, raises
from oxitest._bridge.plugin_loader import (
    EAGER_PROTOCOLS,
    LAZY_PROTOCOLS,
    PluginEntry,
    PluginLoadError,
    _PluginRegistryBuilder,
    activate_deferred_plugins,
    load_plugins,
)
from oxitest.plugin import Plugin


def test_plugin_with_eager_protocol_imported_immediately() -> None:
    """Plugins declaring any eager protocol must be imported immediately."""
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


def test_plugin_without_protocol_declaration_imported_eagerly() -> None:
    """Plugins with no protocol declaration default to eager import."""
    assert PluginEntry.needs_eager_import(None), (
        "no declaration means import eagerly (safe default)"
    )
    assert PluginEntry.needs_eager_import([]), (
        "empty declaration means import eagerly (safe default)"
    )


def test_plugin_with_only_lazy_protocols_not_eager() -> None:
    """Plugins declaring only lazy protocols are deferred at startup."""
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


def test_mixed_protocols_imported_eagerly() -> None:
    """One eager protocol in the declaration forces the whole plugin to load eagerly."""
    assert PluginEntry.needs_eager_import(["fixture_provider", "reporter"]), (
        "presence of one eager protocol forces eager import"
    )


def test_eager_protocols_constant_contains_expected_values() -> None:
    """EAGER_PROTOCOLS contains exactly the four protocols for immediate import."""
    expected = frozenset(
        {"reporter", "collector", "async_backend", "coverage_provider"}
    )
    assert expected == EAGER_PROTOCOLS, (
        f"expected EAGER_PROTOCOLS == {expected!r}, got {EAGER_PROTOCOLS!r}"
    )


def test_lazy_protocols_constant_contains_expected_values() -> None:
    """LAZY_PROTOCOLS contains exactly the five protocols that allow deferred import."""
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


def test_deferred_classmethod_creates_unloaded_entry() -> None:
    """PluginEntry.deferred creates an entry with is_loaded=False and plugin=None."""
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


def test_plugin_entry_default_is_loaded() -> None:
    """A PluginEntry built with a plugin instance is considered immediately loaded."""
    plugin = Plugin()
    entry = PluginEntry(module_name="some.plugin", plugin=plugin)

    assert entry.is_loaded is True, (
        f"default PluginEntry should be loaded, got is_loaded={entry.is_loaded!r}"
    )
    assert entry.declared_protocols is None, (
        f"default declared_protocols should be None, got {entry.declared_protocols!r}"
    )


@oxitest.mark.inprocess
def test_deferred_entry_ensure_loaded_imports_module() -> None:
    """ensure_loaded() imports the module, creates the plugin, marks entry loaded."""
    mod = helpers.common.make_plugin_module("lazy_fixture_plugin", Plugin)
    sys.modules["lazy_fixture_plugin"] = mod
    try:
        entry = PluginEntry.deferred("lazy_fixture_plugin", ["fixture_provider"])
        assert entry.is_loaded is False, (
            "deferred entry should not be loaded yet, "
            f"got is_loaded={entry.is_loaded!r}"
        )

        new_entry, plugin = entry.ensure_loaded()

        assert isinstance(plugin, Plugin), (
            f"ensure_loaded() should return Plugin, got {type(plugin).__name__}"
        )
        assert entry.is_loaded is False, (
            f"original entry should remain unloaded (frozen), got {entry.is_loaded!r}"
        )
        assert new_entry.is_loaded is True, (
            "new entry should be marked loaded after ensure_loaded(), "
            f"got {new_entry.is_loaded!r}"
        )
        assert new_entry.plugin is plugin, (
            "new_entry.plugin should be the same object returned by ensure_loaded()"
        )
    finally:
        sys.modules.pop("lazy_fixture_plugin", None)


def test_ensure_loaded_on_already_loaded_entry_returns_plugin() -> None:
    """ensure_loaded() on a loaded entry returns the plugin without re-importing."""
    plugin = Plugin()
    entry = PluginEntry(module_name="some.plugin", plugin=plugin)

    returned_entry, returned_plugin = entry.ensure_loaded()

    assert returned_entry is entry, (
        "ensure_loaded() on an already-loaded entry should return the same entry"
    )
    assert returned_plugin is plugin, (
        "ensure_loaded() on an already-loaded entry should return the existing plugin"
    )


@oxitest.mark.inprocess
def test_load_plugins_defers_lazy_only_plugin() -> None:
    """load_plugins defers plugins with only lazy protocols (no startup import)."""
    mod = helpers.common.make_plugin_module("lazy_only_plugin", Plugin)
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
def test_load_plugins_eager_imports_plugin_with_eager_protocol() -> None:
    """load_plugins eagerly imports plugins that declare a reporter protocol."""
    mod = helpers.common.make_plugin_module(
        "eager_reporter_plugin", lambda **_: Plugin()
    )
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
def test_load_plugins_eager_imports_plugin_with_no_protocols_declared() -> None:
    """load_plugins eagerly imports plugins with no protocol declaration (default)."""
    mod = helpers.common.make_plugin_module("no_protocols_plugin", lambda **_: Plugin())
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
def test_builder_add_entry_appends_deferred_entry() -> None:
    """_PluginRegistryBuilder.add_entry appends a deferred PluginEntry."""
    builder = _PluginRegistryBuilder()
    entry = PluginEntry.deferred("deferred.plugin", ["log_backend"])

    builder.add_entry(entry)
    registry = builder.build()

    assert len(registry.entries) == 1, (
        f"expected 1 entry after add_entry, got {len(registry.entries)}"
    )
    assert registry.entries[0] == entry, "registered entry should match the original"


@oxitest.mark.inprocess
def test_deferred_fixture_plugin_loaded_via_ensure_loaded() -> None:
    """A deferred fixture plugin can be loaded via ensure_loaded, exposing providers."""

    class FakeToken:
        """Marker type for FakeFixtureProvider."""

    class FakeFixtureProvider:
        @property
        def name(self) -> str:
            return "fake"

        @property
        def fixture_type(self) -> type:
            return FakeToken

        def create(self, **_: Any) -> FakeToken:
            return FakeToken()

        def teardown(self, **_: Any) -> None:
            pass

        @property
        def scope(self) -> str:
            return "each"

        @property
        def autouse(self) -> bool:
            return False

    mod = helpers.common.make_plugin_module(
        "deferred_fixture_plugin",
        lambda: Plugin(fixture_providers=(FakeFixtureProvider(),)),
    )
    sys.modules["deferred_fixture_plugin"] = mod
    try:
        entry = PluginEntry.deferred("deferred_fixture_plugin", ["fixture_provider"])
        new_entry, plugin = entry.ensure_loaded()

        providers = plugin.fixture_providers
        assert len(providers) == 1, f"expected 1 fixture provider, got {len(providers)}"
        assert isinstance(providers[0], FakeFixtureProvider), (
            f"expected FakeFixtureProvider, got {type(providers[0]).__name__}"
        )
        assert new_entry.is_loaded is True, (
            "deferred fixture plugin should be loaded after ensure_loaded, "
            f"got is_loaded={new_entry.is_loaded!r}"
        )
    finally:
        sys.modules.pop("deferred_fixture_plugin", None)


@oxitest.mark.inprocess
def test_builder_builds_registry_with_deferred_non_fixture_plugin() -> None:
    """A deferred non-fixture plugin in the builder yields no fixture_providers."""
    entry = PluginEntry.deferred("lazy_log_plugin", ["log_backend"])
    builder = _PluginRegistryBuilder()
    builder.add_entry(entry)
    registry = builder.build()

    assert registry.fixture_providers == (), (
        "non-fixture deferred plugin should yield no providers, "
        f"got {registry.fixture_providers!r}"
    )
    assert registry.entries[0].is_loaded is False, (
        "non-fixture deferred plugin should remain unloaded, "
        f"got is_loaded={registry.entries[0].is_loaded!r}"
    )


@oxitest.mark.inprocess
def test_deferred_fixture_plugin_activated_in_phase_2() -> None:
    """activate_deferred_plugins loads non-CLI deferred fixture plugins."""

    class FakeFixtureProvider:
        @property
        def name(self) -> str:
            return "fake"

        @property
        def fixture_type(self) -> type:
            return int

        def create(self, **_: Any) -> int:
            return 99

        def teardown(self, **_: Any) -> None:
            pass

    def oxitest_plugin(**_: object) -> Plugin:
        providers: Any = (FakeFixtureProvider(),)
        return Plugin(fixture_providers=providers)

    mod = helpers.common.make_plugin_module("deferred_fx_phase2", oxitest_plugin)
    sys.modules["deferred_fx_phase2"] = mod
    try:
        # Load with declared fixture_provider protocol → deferred (no CLI ext)
        registry = load_plugins(
            ["deferred_fx_phase2"],
            {"deferred_fx_phase2": {"protocols": ["fixture_provider"]}},
        )
        assert not registry.entries[0].is_loaded, (
            "fixture_provider plugin should be deferred after load_plugins"
        )
        assert registry.fixture_providers == (), (
            "deferred plugin's providers should not appear before activation"
        )

        activated = activate_deferred_plugins(registry, "{}", "{}")

        assert activated.entries[0].is_loaded, (
            "fixture_provider plugin should be loaded after activate_deferred_plugins"
        )
        assert len(activated.fixture_providers) == 1, (
            "activated registry should contain the fixture provider, "
            f"got {activated.fixture_providers!r}"
        )
    finally:
        sys.modules.pop("deferred_fx_phase2", None)


def test_ensure_loaded_import_error_raises_plugin_load_error() -> None:
    """ensure_loaded() wraps ImportError in PluginLoadError with 'not found'."""
    entry = PluginEntry.deferred(
        "nonexistent_module_xyz_deferred", ["fixture_provider"]
    )

    with raises(PluginLoadError, match="not found"):
        entry.ensure_loaded()


@oxitest.mark.inprocess
def test_ensure_loaded_missing_entry_point_raises_plugin_load_error() -> None:
    """ensure_loaded() raises PluginLoadError when module has no oxitest_plugin()."""
    mod = types.ModuleType("no_entry_deferred")
    sys.modules["no_entry_deferred"] = mod
    try:
        entry = PluginEntry.deferred("no_entry_deferred", ["fixture_provider"])

        with raises(PluginLoadError, match="no oxitest_plugin\\(\\) function"):
            entry.ensure_loaded()
    finally:
        sys.modules.pop("no_entry_deferred", None)


@oxitest.mark.inprocess
def test_ensure_loaded_entry_raises_wrapped_in_plugin_load_error() -> None:
    """ensure_loaded() wraps exceptions from oxitest_plugin() in PluginLoadError."""

    def broken_entry() -> Never:
        msg = "boom from deferred"
        raise ValueError(msg)

    mod = types.ModuleType("broken_deferred_plugin")
    setattr(mod, "oxitest_plugin", broken_entry)  # noqa: B010 — dynamic module attr
    sys.modules["broken_deferred_plugin"] = mod
    try:
        entry = PluginEntry.deferred("broken_deferred_plugin", ["fixture_provider"])

        with raises(PluginLoadError, match="raised"):
            entry.ensure_loaded()
    finally:
        sys.modules.pop("broken_deferred_plugin", None)
