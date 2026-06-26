"""Typed configuration for the sample infra plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from oxitest import Both, Cli, CliExtension, Conf


@dataclass(frozen=True)
class InfraConfig:
    """Plugin config exercising all source markers and multiple value types."""

    host: Annotated[str, Both(help="Target host", short="H", env="OXITEST_INFRA_HOST")]
    timeout: Annotated[int, Both(help="Connection timeout seconds")] = 30
    verbose: Annotated[bool, Cli(help="Verbose infra output")] = False
    ssh_key: Annotated[str, Conf(help="SSH key path (config only)")] = ""


cli_extension = CliExtension(prefix="infra", config_type=InfraConfig)
