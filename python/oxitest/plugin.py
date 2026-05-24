"""oxitest plugin API.

Plugin authors import from here:
    from oxitest import Plugin
    # or
    from oxitest.plugin import Plugin
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import AsyncBackend


@runtime_checkable
class LogBackend(Protocol):
    """Protocol for log-capture backends."""

    def install(self) -> None:
        """Install the log-capture backend."""
        ...

    def uninstall(self) -> None:
        """Uninstall the log-capture backend."""
        ...

    @property
    def records(self) -> list[Any]:
        """Return captured log records."""
        ...


@runtime_checkable
class FixtureProvider(Protocol):
    """Protocol for plugin-provided fixtures."""

    @property
    def name(self) -> str:
        """Return the fixture name."""
        ...

    @property
    def fixture_type(self) -> type:
        """Return the type this fixture provides."""
        ...

    def create(self, ctx: Any) -> object:
        """Create and return the fixture value."""
        ...

    def teardown(self, value: object) -> None:
        """Tear down the fixture value."""
        ...


@runtime_checkable
class ExecutionWrapper(Protocol):
    """Protocol for marker-triggered execution wrappers."""

    @property
    def marker(self) -> str:
        """Return the marker name that triggers this wrapper."""
        ...

    def wrap(self, test_fn: Any, marker_args: dict[str, Any]) -> Any:
        """Wrap the test function with marker-triggered behavior."""
        ...


@runtime_checkable
class Collector(Protocol):
    """Protocol for custom test collectors."""

    def collect(self, path: str, module: object) -> list[Any]:
        """Collect tests from the given module."""
        ...


@runtime_checkable
class Reporter(Protocol):
    """Protocol for plugin reporters."""

    def test_started(self, item: Any) -> None:
        """Called when a test begins execution."""
        ...

    def test_completed(self, item: Any, outcome: Any, duration_ms: float) -> None:
        """Called when a test finishes with its outcome."""
        ...

    def finish(self, collect_errors: list[Any], interrupted: bool) -> None:
        """Called after all tests complete."""
        ...


@dataclass
class Plugin:
    """Typed declaration of what a plugin provides.

    Returned by the plugin's `oxitest_plugin()` entry point function.
    """

    # Fixture-adjacent hooks (lazy — activated on first use)
    log_backends: list[LogBackend] = field(default_factory=list)
    fixture_providers: list[FixtureProvider] = field(default_factory=list)
    execution_wrappers: list[ExecutionWrapper] = field(default_factory=list)

    # Global hooks (eager — activated at startup)
    collectors: list[Collector] = field(default_factory=list)
    reporters: list[Reporter] = field(default_factory=list)

    # Async runtime backend (at most one across all plugins)
    async_backend: AsyncBackend | None = None
