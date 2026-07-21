"""Tests for two-phase plugin loading: eager CLI discovery + deferred activation."""

from __future__ import annotations

import types
from dataclasses import dataclass
from typing import Annotated

import oxitest
from oxitest import TestContext, helpers
from oxitest._bridge._plugin_config import (
    Both,
    CliExtension,
)
from oxitest._bridge.plugin_loader import (
    ActivatedPluginEntry,
    DeferredPluginEntry,
    _activate_plugin,
    activate_deferred_plugins,
    load_plugins,
)
from oxitest.plugin import Plugin


def _make_plugin_with_extension() -> types.ModuleType:
    """Create a fake plugin module with oxitest_cli_extension."""

    @dataclass(frozen=True)
    class FakeCfg:
        host: Annotated[str, Both(help="host")] = "local"

    call_tracker: list[bool] = []

    def oxitest_plugin(**_: object) -> Plugin:
        call_tracker.append(True)
        return Plugin()

    return helpers.common.make_plugin_module(
        "fake_ext_plugin",
        oxitest_plugin,
        oxitest_cli_extension=CliExtension(prefix="fake", config_type=FakeCfg),
        call_tracker=call_tracker,
    )


@oxitest.mark.inprocess
def test_discover_cli_extensions_reads_attribute(ctx: TestContext) -> None:
    """load_plugins discovers oxitest_cli_extension and stores prefix + descriptors."""
    mod = _make_plugin_with_extension()
    helpers.common.install_module(ctx, "fake_ext_plugin", mod)

    registry = load_plugins(["fake_ext_plugin"], {})
    extensions = registry.cli_extensions
    assert "fake_ext_plugin" in extensions, (
        f"expected fake_ext_plugin in cli_extensions, got {list(extensions)}"
    )
    ext, descs = extensions["fake_ext_plugin"]
    assert ext.prefix == "fake", f"expected prefix 'fake', got '{ext.prefix}'"
    assert len(descs) == 1, f"expected 1 field descriptor, got {len(descs)}"


@oxitest.mark.inprocess
def test_user_prefix_override(ctx: TestContext) -> None:
    """cli_prefix in plugin_settings should override the default extension prefix."""
    mod = _make_plugin_with_extension()
    helpers.common.install_module(ctx, "fake_ext_plugin", mod)

    registry = load_plugins(
        ["fake_ext_plugin"],
        {"fake_ext_plugin": {"cli_prefix": "custom"}},
    )
    ext, _ = registry.cli_extensions["fake_ext_plugin"]
    assert ext.prefix == "custom", f"expected prefix 'custom', got '{ext.prefix}'"


@oxitest.mark.inprocess
def test_plugin_without_extension_has_no_cli(ctx: TestContext) -> None:
    """A plugin without oxitest_cli_extension should not appear in cli_extensions."""
    mod = helpers.common.make_plugin_module("fake_simple", lambda **_: Plugin())
    helpers.common.install_module(ctx, "fake_simple", mod)

    registry = load_plugins(["fake_simple"], {})
    assert "fake_simple" not in registry.cli_extensions, (
        "plugin without oxitest_cli_extension should not appear in cli_extensions"
    )


@oxitest.mark.inprocess
def test_activate_plugin_with_typed_config(ctx: TestContext) -> None:
    """activate_plugin calls oxitest_plugin exactly once with the merged config."""
    mod = _make_plugin_with_extension()
    helpers.common.install_module(ctx, "fake_ext_plugin", mod)

    registry = load_plugins(["fake_ext_plugin"], {})
    plugin = _activate_plugin(
        "fake_ext_plugin",
        cli_extensions=registry.cli_extensions,
        pyproject_values={},
        cli_values={"host": "ssh://test"},
    )
    assert isinstance(plugin, Plugin), f"expected Plugin, got {type(plugin)}"
    call_tracker = mod.call_tracker
    assert len(call_tracker) == 1, "oxitest_plugin should have been called once"


@oxitest.mark.inprocess
def test_activate_deferred_returns_new_registry(ctx: TestContext) -> None:
    """activate_deferred_plugins returns a new registry; the original is unchanged."""
    mod = _make_plugin_with_extension()
    helpers.common.install_module(ctx, "fake_ext_plugin", mod)

    old_registry = load_plugins(["fake_ext_plugin"], {})
    old_entry = old_registry.entries[0]
    assert isinstance(old_entry, DeferredPluginEntry), (
        "deferred plugin should be a DeferredPluginEntry before activation"
    )

    new_registry = activate_deferred_plugins(old_registry, "{}", "{}")

    assert new_registry is not old_registry, (
        "activate_deferred_plugins must return a new registry, not mutate in place"
    )
    assert isinstance(old_registry.entries[0], DeferredPluginEntry), (
        "original registry's entry must remain a DeferredPluginEntry after activation"
    )
    assert isinstance(new_registry.entries[0], ActivatedPluginEntry), (
        "new registry's entry should be an ActivatedPluginEntry after activation"
    )


@oxitest.mark.inprocess
def test_backwards_compat_dict_config(ctx: TestContext) -> None:
    """A legacy plugin accepting dict config should receive plugin_settings as-is."""
    received: dict = {}

    def entry(config: dict[str, str] | None = None) -> Plugin:
        received["config"] = config
        return Plugin()

    mod = helpers.common.make_plugin_module("fake_legacy", entry)
    helpers.common.install_module(ctx, "fake_legacy", mod)

    load_plugins(["fake_legacy"], {"fake_legacy": {"key": "val"}})
    assert received.get("config") == {"key": "val"}, (
        f"legacy plugin should receive dict config, got {received.get('config')}"
    )
