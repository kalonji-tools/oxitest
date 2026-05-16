"""oxitest plugin API.

Plugin authors import from here:
    from oxitest import Plugin
    # or
    from oxitest.plugin import Plugin
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LogBackend(Protocol):
    """Protocol for log-capture backends."""

    def install(self) -> None: ...
    def uninstall(self) -> None: ...

    @property
    def records(self) -> list[Any]: ...


@runtime_checkable
class FixtureProvider(Protocol):
    """Protocol for plugin-provided fixtures."""

    @property
    def name(self) -> str: ...

    @property
    def fixture_type(self) -> type: ...

    def create(self, ctx: Any) -> object: ...
    def teardown(self, value: object) -> None: ...


@runtime_checkable
class ExecutionWrapper(Protocol):
    """Protocol for marker-triggered execution wrappers."""

    @property
    def marker(self) -> str: ...

    def wrap(self, test_fn: Any, marker_args: dict[str, Any]) -> Any: ...


@runtime_checkable
class Collector(Protocol):
    """Protocol for custom test collectors."""

    def collect(self, path: str, module: object) -> list[Any]: ...


@runtime_checkable
class Reporter(Protocol):
    """Protocol for plugin reporters."""

    def test_started(self, item: Any) -> None: ...
    def test_completed(self, item: Any, outcome: Any, duration_ms: float) -> None: ...
    def finish(self, collect_errors: list[Any], interrupted: bool) -> None: ...


@dataclass
class Plugin:
    """Typed declaration of what a plugin provides.

    Returned by the plugin's ``oxitest_plugin()`` entry point function.
    """

    # Fixture-adjacent hooks (lazy — activated on first use)
    log_backends: list[LogBackend] = field(default_factory=list)
    fixture_providers: list[FixtureProvider] = field(default_factory=list)
    execution_wrappers: list[ExecutionWrapper] = field(default_factory=list)

    # Global hooks (eager — activated at startup)
    collectors: list[Collector] = field(default_factory=list)
    reporters: list[Reporter] = field(default_factory=list)
