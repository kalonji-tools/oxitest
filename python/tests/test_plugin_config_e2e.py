"""End-to-end test for the full plugin config flow: introspect -> merge -> construct."""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from typing import Annotated

import oxitest
from oxitest import Both, Cli, CliExtension, Conf, Plugin
from oxitest._bridge._plugin_config import introspect_config, merge_config
from oxitest._bridge.plugin_loader import load_plugins


@dataclass(frozen=True)
class TestConfig:
    host: Annotated[str, Both(short="H", help="Target host", env="TEST_E2E_HOST")] = (
        "local://"
    )
    path: Annotated[str | None, Conf(help="Config path")] = None
    verbose: Annotated[bool, Cli(help="Verbose output")] = False


@oxitest.mark.inprocess
def test_full_flow_introspect_merge_construct():
    descs = introspect_config(TestConfig)
    config = merge_config(
        TestConfig,
        descs,
        pyproject_values={"host": "pyproject://", "path": "/etc/test"},
        cli_values={"host": "cli://", "verbose": True},
        env_values={},
    )
    assert config.host == "cli://", f"CLI should override pyproject, got {config.host}"
    assert config.path == "/etc/test", (
        f"Conf field should use pyproject, got {config.path}"
    )
    assert config.verbose is True, (
        f"Cli field should use CLI value, got {config.verbose}"
    )


@oxitest.mark.inprocess
def test_plugin_loader_discovers_and_activates_typed_config():
    mod = types.ModuleType("e2e_plugin")
    mod.oxitest_cli_extension = CliExtension(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        prefix="e2e", config_type=TestConfig
    )

    received_configs: list[TestConfig] = []

    def oxitest_plugin(*, config):
        received_configs.append(config)
        return Plugin()

    mod.oxitest_plugin = oxitest_plugin  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    sys.modules["e2e_plugin"] = mod

    try:
        registry = load_plugins(
            ["e2e_plugin"], {"e2e_plugin": {"path": "/from/pyproject"}}
        )
        assert "e2e_plugin" in registry.cli_extensions, (
            "plugin should have CLI extensions registered"
        )

        registry.activate_plugin(
            "e2e_plugin",
            pyproject_values={"path": "/from/pyproject"},
            cli_values={"host": "ssh://activated"},
        )

        assert len(received_configs) == 1, (
            "oxitest_plugin should be called once on activation"
        )
        cfg = received_configs[0]
        assert isinstance(cfg, TestConfig), (
            f"config should be TestConfig, got {type(cfg)}"
        )
        assert cfg.host == "ssh://activated", f"expected CLI override, got {cfg.host}"
        assert cfg.path == "/from/pyproject", (
            f"expected pyproject value, got {cfg.path}"
        )
        assert cfg.verbose is False, (
            f"verbose should default to False, got {cfg.verbose}"
        )
    finally:
        sys.modules.pop("e2e_plugin", None)
