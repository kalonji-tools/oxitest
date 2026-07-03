"""Protocol implementations for the sample metrics plugin."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

from ._config import MetricsConfig


class MetricsCollector:
    """Fixture value provided by MetricsCollectorProvider."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, name: str, value: float) -> None:
        self.events.append({"name": name, "value": value})


class MetricsCollectorProvider:
    """FixtureProvider: injects a MetricsCollector into tests."""

    @property
    def name(self) -> str:
        return "metrics_collector"

    @property
    def fixture_type(self) -> type:
        return MetricsCollector

    def create(self, ctx: Any) -> MetricsCollector:
        return MetricsCollector()

    def teardown(self, value: object) -> None:
        pass

    @property
    def scope(self) -> str:
        return "each"

    @property
    def autouse(self) -> bool:
        return False


class MetricsReporter:
    """Reporter: writes test events to a JSON file."""

    def __init__(self, output_path: str) -> None:
        self._path = Path(output_path)
        self._events: list[dict[str, Any]] = []

    def test_started(self, item: Any) -> None:
        self._events.append({"event": "started", "item": str(item)})

    def test_completed(self, item: Any, outcome: Any, duration_ms: float) -> None:
        self._events.append(
            {
                "event": "completed",
                "item": str(item),
                "outcome": str(outcome),
                "duration_ms": duration_ms,
            }
        )

    def finish(self, collect_errors: list[Any], *, interrupted: bool) -> None:
        self._events.append({"event": "finish", "interrupted": interrupted})
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._events, indent=2))


class BenchCollector:
    """Collector: discovers bench_* functions as test items."""

    def collect(self, path: str, module: object) -> list[Any]:
        from oxitest._bridge.result import CollectedItem

        items = []
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("bench_"):
                lineno = inspect.getsourcelines(obj)[1]
                items.append(
                    CollectedItem(
                        fn_name=name,
                        lineno=lineno,
                        markers=(),
                        param_id=None,
                        param_values=(),
                    )
                )
        return items


class MetricsCoverageProvider:
    """CoverageProvider: stub that writes a marker file to prove it ran."""

    def __init__(self, output_dir: str) -> None:
        self._dir = Path(output_dir)

    def start(self, config: dict[str, Any]) -> None:
        marker = self._dir / "coverage_started.txt"
        marker.write_text("started")

    def stop(self) -> None:
        marker = Path.cwd() / "coverage_stopped.txt"
        marker.write_text("stopped")

    def report(self, fmt: Any) -> int:
        return 0


def build_plugin(
    config: MetricsConfig,
) -> tuple[
    MetricsCollectorProvider, MetricsReporter, BenchCollector, MetricsCoverageProvider
]:
    """Construct protocol implementations from typed config."""
    return (
        MetricsCollectorProvider(),
        MetricsReporter(config.output),
        BenchCollector(),
        MetricsCoverageProvider(str(Path(config.output).parent)),
    )
