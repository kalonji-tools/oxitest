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
    from oxitest._bridge._debugger import DebuggerBackend


@runtime_checkable
class LogBackend(Protocol):
    """Protocol for log-capture backends."""

    def install(self) -> None:
        """Attach the log handler and begin capturing records.

        Called once before each test that requests the log fixture.  After
        this call, any log output routed through the backend's handler will
        appear in `records`.
        """
        ...

    def uninstall(self) -> None:
        """Detach the log handler and stop capturing.

        Called after each test completes.  The backend should remove any
        handlers added in `install` and clear internal state ready for the
        next test.
        """
        ...

    @property
    def records(self) -> list[Any]:
        """Captured log records accumulated since the last `install` call.

        Returns a list of backend-specific record objects (e.g.
        `logging.LogRecord` for the stdlib backend).  The list is reset on
        each `install`.
        """
        ...


@runtime_checkable
class FixtureProvider(Protocol):
    """Protocol for plugin-provided fixtures."""

    @property
    def name(self) -> str:
        """Unique fixture name used in error messages and diagnostics.

        This name is not used for injection matching — oxitest matches plugin
        fixtures by `fixture_type`, not by name.
        """
        ...

    @property
    def fixture_type(self) -> type:
        """The Python type that triggers injection of this fixture.

        When a test parameter is annotated ``param: Fixture[T]``, oxitest
        checks each registered provider to see if ``provider.fixture_type is T``.
        The first match wins and `create` is called to produce the value.
        """
        ...

    def create(self, ctx: Any) -> object:
        """Instantiate and return the fixture value for a single test.

        Args:
            ctx: Reserved for future use; currently always `None`.

        Returns:
            The fixture object that will be injected into the test.
        """
        ...

    def teardown(self, value: object) -> None:
        """Release resources held by the fixture value.

        Called after the test completes, regardless of pass or fail.

        Args:
            value: The object previously returned by `create`.
        """
        ...


@runtime_checkable
class ExecutionWrapper(Protocol):
    """Protocol for marker-triggered execution wrappers."""

    @property
    def marker(self) -> str:
        """The marker name that activates this wrapper (e.g. ``"slow"``).

        When a test is decorated with ``@oxitest.mark.<name>`` and `name`
        matches this value, `wrap` is called instead of running the test
        directly.
        """
        ...

    def wrap(self, test_fn: Any, marker_args: dict[str, Any]) -> Any:
        """Execute the test, applying marker-driven behaviour around it.

        `test_fn` is a zero-argument callable that runs the test and returns a
        `TestResult`.  The wrapper must call `test_fn()` at most once and return
        its result (possibly transformed).

        Args:
            test_fn: Zero-argument callable that runs the test.
            marker_args: Combined positional (keyed by index) and keyword
                arguments from the marker declaration.

        Returns:
            A `TestResult` for the test.
        """
        ...


@runtime_checkable
class Collector(Protocol):
    """Protocol for custom test collectors."""

    def collect(self, path: str, module: object) -> list[Any]:
        """Collect test items from an already-imported module.

        Args:
            path: Absolute filesystem path to the test file.
            module: The imported module object.

        Returns:
            A list of `CollectedItem`-compatible objects describing the tests
            found.  Return an empty list if the collector finds nothing.
        """
        ...


@runtime_checkable
class Reporter(Protocol):
    """Protocol for plugin reporters."""

    def test_started(self, item: Any) -> None:
        """Called immediately before a test begins executing.

        Args:
            item: A `CollectedItem`-compatible object identifying the test
                (node ID, module path, function name).
        """
        ...

    def test_completed(self, item: Any, outcome: Any, duration_ms: float) -> None:
        """Called immediately after a test finishes, whether it passed or failed.

        Args:
            item: The same `CollectedItem`-compatible object passed to
                `test_started`.
            outcome: A `TestResult`-compatible object with the test status
                and any failure information.
            duration_ms: Wall-clock time the test took, in milliseconds.
        """
        ...

    def finish(self, collect_errors: list[Any], interrupted: bool) -> None:
        """Called once after all tests have completed and teardown is done.

        Args:
            collect_errors: List of errors encountered during collection
                (module import failures, etc.).  Empty when collection
                succeeded cleanly.
            interrupted: `True` if the run was cut short (e.g. ``--maxfail``
                threshold reached or a signal was received).
        """
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

    # Debugger backend (at most one across all plugins)
    debugger_backend: DebuggerBackend | None = None
