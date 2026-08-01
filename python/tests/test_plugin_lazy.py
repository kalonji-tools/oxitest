"""Tests for lazy plugin module import."""

from __future__ import annotations

import types
from typing import Any, Never

import oxitest
from oxitest import TestContext, raises
from oxitest._bridge.plugin_loader import (
    EAGER_PROTOCOLS,
    LAZY_PROTOCOLS,
    ActivatedPluginEntry,
    DeferredPluginEntry,
    PluginLoadError,
    _PluginRegistryBuilder,
    activate_deferred_plugins,
    activate_entry,
    load_plugins,
    needs_eager_import,
)
from oxitest.plugin import Plugin
from tests import helpers


def test_plugin_with_eager_protocol_imported_immediately() -> None:
    """Plugins declaring any eager protocol must be imported immediately."""
    assert needs_eager_import(("reporter",)), "reporter is an eager protocol"
    assert needs_eager_import(("collector",)), "collector is an eager protocol"
    assert needs_eager_import(("async_backend",)), "async_backend is an eager protocol"
    assert needs_eager_import(("coverage_provider",)), (
        "coverage_provider is an eager protocol"
    )


def test_plugin_without_protocol_declaration_imported_eagerly() -> None:
    """Plugins with no protocol declaration default to eager import."""
    assert needs_eager_import(()), "empty tuple means import eagerly (safe default)"


def test_plugin_with_only_lazy_protocols_not_eager() -> None:
    """Plugins declaring only lazy protocols are deferred at startup."""
    assert not needs_eager_import(("fixture_provider",)), (
        "fixture_provider is a lazy protocol"
    )
    assert not needs_eager_import(("log_backend",)), "log_backend is a lazy protocol"
    assert not needs_eager_import(("execution_wrapper",)), (
        "execution_wrapper is a lazy protocol"
    )
    assert not needs_eager_import(("debugger_backend",)), (
        "debugger_backend is a lazy protocol"
    )
    assert not needs_eager_import(("fixture_provider", "log_backend")), (
        "all-lazy combination should not be eager"
    )


def test_mixed_protocols_imported_eagerly() -> None:
    """One eager protocol in the declaration forces the whole plugin to load eagerly."""
    assert needs_eager_import(("fixture_provider", "reporter")), (
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


def test_deferred_plugin_entry_is_not_activated() -> None:
    """DeferredPluginEntry creates an entry that is not yet imported."""
    entry = DeferredPluginEntry(
        module_name="some.plugin", declared_protocols=("fixture_provider",)
    )

    assert entry.module_name == "some.plugin", (
        f"expected module_name 'some.plugin', got {entry.module_name!r}"
    )
    assert not isinstance(entry, ActivatedPluginEntry), (
        "DeferredPluginEntry must not be an ActivatedPluginEntry"
    )
    assert entry.declared_protocols == ("fixture_provider",), (
        "expected declared_protocols=('fixture_provider',), "
        f"got {entry.declared_protocols!r}"
    )


def test_activated_plugin_entry_default_declared_protocols_is_empty() -> None:
    """An ActivatedPluginEntry built with a plugin instance is immediately activated."""
    plugin = Plugin()
    entry = ActivatedPluginEntry(module_name="some.plugin", plugin=plugin)

    assert isinstance(entry, ActivatedPluginEntry), (
        f"ActivatedPluginEntry must be an ActivatedPluginEntry, got {type(entry)!r}"
    )
    assert entry.declared_protocols == (), (
        "default declared_protocols should be empty tuple, "
        f"got {entry.declared_protocols!r}"
    )


@oxitest.mark.inprocess
def test_activate_entry_imports_module(ctx: TestContext) -> None:
    """activate_entry() imports the module and returns an ActivatedPluginEntry."""
    mod = helpers.make_plugin_module("lazy_fixture_plugin", Plugin)
    helpers.install_module(ctx, "lazy_fixture_plugin", mod)

    entry = DeferredPluginEntry(
        module_name="lazy_fixture_plugin", declared_protocols=("fixture_provider",)
    )
    assert not isinstance(entry, ActivatedPluginEntry), (
        "DeferredPluginEntry must not be an ActivatedPluginEntry before activation"
    )

    new_entry = activate_entry(entry)

    assert isinstance(new_entry, ActivatedPluginEntry), (
        "activate_entry() must return ActivatedPluginEntry, "
        f"got {type(new_entry).__name__}"
    )
    assert isinstance(new_entry.plugin, Plugin), (
        "activate_entry() must set a Plugin instance, "
        f"got {type(new_entry.plugin).__name__}"
    )
    assert not isinstance(entry, ActivatedPluginEntry), (
        "original DeferredPluginEntry must remain deferred (frozen)"
    )


def test_activated_plugin_entry_plugin_accessible() -> None:
    """ActivatedPluginEntry exposes its plugin instance directly."""
    plugin = Plugin()
    entry = ActivatedPluginEntry(module_name="some.plugin", plugin=plugin)

    assert entry.plugin is plugin, (
        "ActivatedPluginEntry.plugin must return the same object by identity"
    )


@oxitest.mark.inprocess
def test_load_plugins_defers_lazy_only_plugin(ctx: TestContext) -> None:
    """load_plugins defers plugins with only lazy protocols (no startup import)."""
    mod = helpers.make_plugin_module("lazy_only_plugin", Plugin)
    helpers.install_module(ctx, "lazy_only_plugin", mod)

    registry = load_plugins(
        ["lazy_only_plugin"],
        {"lazy_only_plugin": {"protocols": ["fixture_provider"]}},
    )
    assert len(registry.entries) == 1, f"expected 1 entry, got {len(registry.entries)}"
    entry = registry.entries[0]
    assert entry.module_name == "lazy_only_plugin", (
        f"expected module_name 'lazy_only_plugin', got {entry.module_name!r}"
    )
    assert isinstance(entry, DeferredPluginEntry), (
        f"lazy-only plugin should be deferred, got {type(entry).__name__}"
    )


@oxitest.mark.inprocess
def test_load_plugins_eager_imports_plugin_with_eager_protocol(
    ctx: TestContext,
) -> None:
    """load_plugins eagerly imports plugins that declare a reporter protocol."""
    mod = helpers.make_plugin_module("eager_reporter_plugin", lambda **_: Plugin())
    helpers.install_module(ctx, "eager_reporter_plugin", mod)

    registry = load_plugins(
        ["eager_reporter_plugin"],
        {"eager_reporter_plugin": {"protocols": ["reporter"]}},
    )
    assert len(registry.entries) == 1, f"expected 1 entry, got {len(registry.entries)}"
    entry = registry.entries[0]
    assert isinstance(entry, ActivatedPluginEntry), (
        f"reporter plugin should be eagerly loaded, got {type(entry).__name__}"
    )


@oxitest.mark.inprocess
def test_load_plugins_eager_imports_plugin_with_no_protocols_declared(
    ctx: TestContext,
) -> None:
    """load_plugins eagerly imports plugins with no protocol declaration (default)."""
    mod = helpers.make_plugin_module("no_protocols_plugin", lambda **_: Plugin())
    helpers.install_module(ctx, "no_protocols_plugin", mod)

    registry = load_plugins(["no_protocols_plugin"], {})
    assert len(registry.entries) == 1, f"expected 1 entry, got {len(registry.entries)}"
    entry = registry.entries[0]
    assert isinstance(entry, ActivatedPluginEntry), (
        f"plugin without protocol declaration should be eagerly loaded, "
        f"got {type(entry).__name__}"
    )


@oxitest.mark.inprocess
def test_builder_add_entry_appends_deferred_entry() -> None:
    """_PluginRegistryBuilder.add_entry appends a deferred DeferredPluginEntry."""
    builder = _PluginRegistryBuilder()
    entry = DeferredPluginEntry(
        module_name="deferred.plugin", declared_protocols=("log_backend",)
    )

    builder.add_entry(entry)
    registry = builder.build()

    assert len(registry.entries) == 1, (
        f"expected 1 entry after add_entry, got {len(registry.entries)}"
    )
    assert registry.entries[0] == entry, "registered entry should match the original"


@oxitest.mark.inprocess
def test_deferred_fixture_plugin_loaded_via_activate_entry(ctx: TestContext) -> None:
    """A deferred fixture plugin loaded via activate_entry() exposes its providers."""

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

    mod = helpers.make_plugin_module(
        "deferred_fixture_plugin",
        lambda: Plugin(fixture_providers=(FakeFixtureProvider(),)),
    )
    helpers.install_module(ctx, "deferred_fixture_plugin", mod)

    entry = DeferredPluginEntry(
        module_name="deferred_fixture_plugin", declared_protocols=("fixture_provider",)
    )
    new_entry = activate_entry(entry)

    providers = new_entry.plugin.fixture_providers
    assert len(providers) == 1, f"expected 1 fixture provider, got {len(providers)}"
    assert isinstance(providers[0], FakeFixtureProvider), (
        f"expected FakeFixtureProvider, got {type(providers[0]).__name__}"
    )
    assert isinstance(new_entry, ActivatedPluginEntry), (
        "deferred fixture plugin should be activated after activate_entry, "
        f"got {type(new_entry).__name__}"
    )


@oxitest.mark.inprocess
def test_builder_builds_registry_with_deferred_non_fixture_plugin() -> None:
    """A deferred non-fixture plugin in the builder yields no fixture_providers."""
    entry = DeferredPluginEntry(
        module_name="lazy_log_plugin", declared_protocols=("log_backend",)
    )
    builder = _PluginRegistryBuilder()
    builder.add_entry(entry)
    registry = builder.build()

    assert registry.fixture_providers == (), (
        "non-fixture deferred plugin should yield no providers, "
        f"got {registry.fixture_providers!r}"
    )
    assert isinstance(registry.entries[0], DeferredPluginEntry), (
        "non-fixture deferred plugin should remain a DeferredPluginEntry, "
        f"got {type(registry.entries[0]).__name__}"
    )


@oxitest.mark.inprocess
def test_deferred_fixture_plugin_activated_in_phase_2(ctx: TestContext) -> None:
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

    mod = helpers.make_plugin_module("deferred_fx_phase2", oxitest_plugin)
    helpers.install_module(ctx, "deferred_fx_phase2", mod)

    # Load with declared fixture_provider protocol → deferred (no CLI ext)
    registry = load_plugins(
        ["deferred_fx_phase2"],
        {"deferred_fx_phase2": {"protocols": ["fixture_provider"]}},
    )
    assert isinstance(registry.entries[0], DeferredPluginEntry), (
        "fixture_provider plugin should be deferred after load_plugins"
    )
    assert registry.fixture_providers == (), (
        "deferred plugin's providers should not appear before activation"
    )

    activated = activate_deferred_plugins(registry, "{}", "{}")

    assert isinstance(activated.entries[0], ActivatedPluginEntry), (
        "fixture_provider plugin should be activated after activate_deferred_plugins"
    )
    assert len(activated.fixture_providers) == 1, (
        "activated registry should contain the fixture provider, "
        f"got {activated.fixture_providers!r}"
    )


def test_activate_entry_import_error_raises_plugin_load_error() -> None:
    """activate_entry() wraps ImportError in PluginLoadError with 'not found'."""
    entry = DeferredPluginEntry(
        module_name="nonexistent_module_xyz_deferred",
        declared_protocols=("fixture_provider",),
    )

    with raises(PluginLoadError, match="not found"):
        activate_entry(entry)


@oxitest.mark.inprocess
def test_activate_entry_missing_entry_point_raises_plugin_load_error(
    ctx: TestContext,
) -> None:
    """activate_entry() raises PluginLoadError when module has no oxitest_plugin()."""
    mod = types.ModuleType("no_entry_deferred")
    helpers.install_module(ctx, "no_entry_deferred", mod)

    entry = DeferredPluginEntry(
        module_name="no_entry_deferred", declared_protocols=("fixture_provider",)
    )

    with raises(PluginLoadError, match="no oxitest_plugin\\(\\) function"):
        activate_entry(entry)


@oxitest.mark.inprocess
def test_activate_entry_exception_wrapped_in_plugin_load_error(
    ctx: TestContext,
) -> None:
    """activate_entry() wraps exceptions from oxitest_plugin() in PluginLoadError."""

    def broken_entry() -> Never:
        msg = "boom from deferred"
        raise ValueError(msg)

    mod = types.ModuleType("broken_deferred_plugin")
    setattr(mod, "oxitest_plugin", broken_entry)  # noqa: B010 — dynamic module attr
    helpers.install_module(ctx, "broken_deferred_plugin", mod)

    entry = DeferredPluginEntry(
        module_name="broken_deferred_plugin", declared_protocols=("fixture_provider",)
    )

    with raises(PluginLoadError, match="raised"):
        activate_entry(entry)
