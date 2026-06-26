"""Sample infra plugin for integration testing.

Exercises: FixtureProvider, LogBackend, ExecutionWrapper, typed CLI config.
"""

from __future__ import annotations

import json
from pathlib import Path

from oxitest import Plugin

from ._config import InfraConfig, cli_extension

oxitest_cli_extension = cli_extension


def oxitest_plugin(*, config: InfraConfig | None = None) -> Plugin:
    """Entry point — defers heavy import until called."""
    from ._plugin import build_plugin

    if config is None:
        config = InfraConfig(host="localhost")

    marker = Path.cwd() / "infra_config_received.json"
    marker.write_text(
        json.dumps(
            {
                "host": config.host,
                "timeout": config.timeout,
                "verbose": config.verbose,
                "ssh_key": config.ssh_key,
            }
        )
    )

    provider, log_backend, wrapper = build_plugin(config)
    return Plugin(
        fixture_providers=(provider,),
        log_backends=(log_backend,),
        execution_wrappers=(wrapper,),
    )
