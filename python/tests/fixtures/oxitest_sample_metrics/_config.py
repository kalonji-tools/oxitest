"""Typed configuration for the sample metrics plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from oxitest import Both, Cli, CliExtension, Conf


@dataclass(frozen=True)
class MetricsConfig:
    """Plugin config exercising str, float types and all source markers."""

    output: Annotated[str, Both(help="Metrics output path", short="o")]
    format: Annotated[str, Cli(help="Output format")] = "json"
    threshold: Annotated[float, Conf(help="Slow test threshold seconds")] = 1.0


cli_extension = CliExtension(prefix="metrics", config_type=MetricsConfig)
