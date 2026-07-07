"""Tests for two-phase plugin loading: eager CLI discovery + deferred activation."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Annotated

import oxitest
from oxitest._bridge._plugin_config import (
    Both,
    CliExtension,
)
from oxitest._bridge.plugin_loader import load_plugins
from oxitest.plugin import Plugin


def _make_plugin_with_extension() -> types.ModuleType:
    """Create a fake plugin module with oxitest_cli_extension."""

    @dataclass(frozen=True)
    class FakeCfg:
        host: Annotated[str, Both(help="host")] = "local"

    mod = types.ModuleType("fake_ext_plugin")
    setattr(
        mod, "oxitest_cli_extension", CliExtension(prefix="fake", config_type=FakeCfg)
    )

    call_tracker: list[bool] = []

    def oxitest_plugin(**_: object) -> Plugin:
        call_tracker.append(True)
        return Plugin()

    setattr(mod, "oxitest_plugin", oxitest_plugin)
    setattr(mod, "_call_tracker", call_tracker)
    return mod


@oxitest.mark.inprocess
def test_discover_cli_extensions_reads_attribute() -> None:
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
    mod = types.ModuleType("fake_simple")
    setattr(mod, "oxitest_plugin", lambda **_: Plugin())
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
    mod = _make_plugin_with_extension()
    sys.modules["fake_ext_plugin"] = mod
    try:
        registry = load_plugins(["fake_ext_plugin"], {})
        plugin = registry.activate_plugin(
            "fake_ext_plugin",
            pyproject_values={},
            cli_values={"host": "ssh://test"},
        )
        assert isinstance(plugin, Plugin), f"expected Plugin, got {type(plugin)}"
        call_tracker = getattr(mod, "_call_tracker")
        assert len(call_tracker) == 1, "oxitest_plugin should have been called once"
    finally:
        sys.modules.pop("fake_ext_plugin", None)


@oxitest.mark.inprocess
def test_backwards_compat_dict_config() -> None:
    received: dict = {}

    def entry(config: dict[str, str] | None = None) -> Plugin:
        received["config"] = config
        return Plugin()

    mod = types.ModuleType("fake_legacy")
    setattr(mod, "oxitest_plugin", entry)
    sys.modules["fake_legacy"] = mod
    try:
        load_plugins(["fake_legacy"], {"fake_legacy": {"key": "val"}})
        assert received.get("config") == {"key": "val"}, (
            f"legacy plugin should receive dict config, got {received.get('config')}"
        )
    finally:
        sys.modules.pop("fake_legacy", None)
