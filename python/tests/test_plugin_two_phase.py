"""Tests for two-phase plugin loading: eager CLI discovery + deferred activation."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Annotated

import oxitest
from oxitest import helpers
from oxitest._bridge._plugin_config import (
    Both,
    CliExtension,
)
from oxitest._bridge.plugin_loader import (
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
def test_discover_cli_extensions_reads_attribute() -> None:
    """load_plugins discovers oxitest_cli_extension and stores prefix + descriptors."""
    mod = _make_plugin_with_extension()
    sys.modules["fake_ext_plugin"] = mod
    try:
        registry = load_plugins(["fake_ext_plugin"], {})
        extensions = registry.cli_extensions
        assert "fake_ext_plugin" in extensions, (
            f"expected fake_ext_plugin in cli_extensions, got {list(extensions)}"
        )
        ext, descs = extensions["fake_ext_plugin"]
        assert ext.prefix == "fake", f"expected prefix 'fake', got '{ext.prefix}'"
        assert len(descs) == 1, f"expected 1 field descriptor, got {len(descs)}"
    finally:
        sys.modules.pop("fake_ext_plugin", None)


@oxitest.mark.inprocess
def test_user_prefix_override() -> None:
    """cli_prefix in plugin_settings should override the default extension prefix."""
    mod = _make_plugin_with_extension()
    sys.modules["fake_ext_plugin"] = mod
    try:
        registry = load_plugins(
            ["fake_ext_plugin"],
            {"fake_ext_plugin": {"cli_prefix": "custom"}},
        )
        ext, _ = registry.cli_extensions["fake_ext_plugin"]
        assert ext.prefix == "custom", f"expected prefix 'custom', got '{ext.prefix}'"
    finally:
        sys.modules.pop("fake_ext_plugin", None)


@oxitest.mark.inprocess
def test_plugin_without_extension_has_no_cli() -> None:
    """A plugin without oxitest_cli_extension should not appear in cli_extensions."""
    mod = helpers.common.make_plugin_module("fake_simple", lambda **_: Plugin())
    sys.modules["fake_simple"] = mod
    try:
        registry = load_plugins(["fake_simple"], {})
        assert "fake_simple" not in registry.cli_extensions, (
            "plugin without oxitest_cli_extension should not appear in cli_extensions"
        )
    finally:
        sys.modules.pop("fake_simple", None)


@oxitest.mark.inprocess
def test_activate_plugin_with_typed_config() -> None:
    """activate_plugin calls oxitest_plugin exactly once with the merged config."""
    mod = _make_plugin_with_extension()
    sys.modules["fake_ext_plugin"] = mod
    try:
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
    finally:
        sys.modules.pop("fake_ext_plugin", None)


@oxitest.mark.inprocess
def test_activate_deferred_returns_new_registry() -> None:
    """activate_deferred_plugins returns a new registry; the original is unchanged."""
    mod = _make_plugin_with_extension()
    sys.modules["fake_ext_plugin"] = mod
    try:
        old_registry = load_plugins(["fake_ext_plugin"], {})
        old_entry = old_registry.entries[0]
        assert not old_entry.is_loaded, (
            "deferred plugin should not be loaded before activation"
        )

        new_registry = activate_deferred_plugins(old_registry, "{}", "{}")

        assert new_registry is not old_registry, (
            "activate_deferred_plugins must return a new registry, not mutate in place"
        )
        assert not old_registry.entries[0].is_loaded, (
            "original registry's entry must remain unloaded after activation"
        )
        assert new_registry.entries[0].is_loaded, (
            "new registry's entry should be loaded after activation"
        )
    finally:
        sys.modules.pop("fake_ext_plugin", None)


@oxitest.mark.inprocess
def test_backwards_compat_dict_config() -> None:
    """A legacy plugin accepting dict config should receive plugin_settings as-is."""
    received: dict = {}

    def entry(config: dict[str, str] | None = None) -> Plugin:
        received["config"] = config
        return Plugin()

    mod = helpers.common.make_plugin_module("fake_legacy", entry)
    sys.modules["fake_legacy"] = mod
    try:
        load_plugins(["fake_legacy"], {"fake_legacy": {"key": "val"}})
        assert received.get("config") == {"key": "val"}, (
            f"legacy plugin should receive dict config, got {received.get('config')}"
        )
    finally:
        sys.modules.pop("fake_legacy", None)
