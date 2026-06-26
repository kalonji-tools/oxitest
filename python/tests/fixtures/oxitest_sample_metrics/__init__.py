"""Sample metrics plugin for integration testing.

Exercises: Reporter, Collector, CoverageProvider, typed CLI config.
"""

from __future__ import annotations

import json
from pathlib import Path

from oxitest import Plugin

from ._config import MetricsConfig, cli_extension

oxitest_cli_extension = cli_extension


def oxitest_plugin(*, config: MetricsConfig | None = None) -> Plugin:
    """Entry point — defers heavy import until called."""
    from ._plugin import build_plugin

    if config is None:
        config = MetricsConfig(output="metrics.json")

    marker = Path.cwd() / "metrics_config_received.json"
    marker.write_text(
        json.dumps(
            {
                "output": config.output,
                "format": config.format,
                "threshold": config.threshold,
            }
        )
    )

    provider, reporter, collector, coverage = build_plugin(config)
    return Plugin(
        fixture_providers=[provider],
        reporters=[reporter],
        collectors=[collector],
        coverage_provider=coverage,
    )
