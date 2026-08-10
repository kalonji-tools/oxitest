"""oxitest plugin API.

Plugin authors import from here:
    from oxitest import Plugin
    # or
    from oxitest.plugin import Plugin
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from oxitest._bridge._async_backend import _NULL_ASYNC_BACKEND, AsyncBackend
from oxitest._bridge._coverage import _NULL_COVERAGE
from oxitest._bridge._debugger import _NULL_DEBUGGER, DebuggerBackend
from oxitest._bridge.result import (
    SkippedResult,
    StatusKind,
    TestResult,
    WarnedResult,
    XFailedResult,
)

__all__ = [
    "SkippedResult",
    "StatusKind",
    "TestResult",
    "WarnedResult",
    "XFailedResult",
    "skipped",
    "warned",
    "xfailed",
]

if TYPE_CHECKING:
    from oxitest._bridge._coverage import CovReportFormat
    from oxitest._bridge.result import CollectedItem


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
    def records(self) -> Sequence[Any]:
        """Captured log records accumulated since the last `install` call.

        Returns a list of backend-specific record objects (e.g.
        `logging.LogRecord` for the stdlib backend).  The list is reset on
        each `install`.

        .. important::
            Each record object **must** provide the following attributes,
            matching the shape of ``logging.LogRecord``:

            - ``created`` (``float``) — timestamp (``time.time()`` epoch seconds)
            - ``levelname`` (``str``) — severity name (e.g. ``"WARNING"``)
            - ``getMessage()`` (``Callable[[], str]``) — formatted message text

            The built-in ``LogCapture`` consumer sorts by ``created`` and
            formats output using ``levelname`` and ``getMessage()``.  Records
            missing these attributes will raise ``AttributeError`` at runtime.
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

    def create(self, *, ctx: Any) -> object:
        """Instantiate and return the fixture value for a single test.

        Args:
            ctx: Reserved for future use; currently always `None`.

        Returns:
            The fixture object that will be injected into the test.

        """
        ...

    def teardown(self, *, value: object) -> None:
        """Release resources held by the fixture value.

        Called after the test completes, regardless of pass or fail.

        Args:
            value: The object previously returned by `create`.

        """
        ...

    @property
    def scope(self) -> str:
        """Fixture scope: 'each' (per test) or 'session'.

        `'session'` is built once per **task group** — the
        unit of work a worker picks up, which is a single module unless a
        `lifetime="package"` declaration merges a subtree. Neither is once per
        run, and neither is once per worker process.

        The *mechanism* changed in #1777, though the rate above did not. A
        worker now builds **one** `FixtureSession` for the whole process rather
        than a fresh one per task, so these caches no longer expire by being
        thrown away with their session — they are drained by `end_task`, which
        a worker calls once per task group. The tier that is genuinely once per
        process is `lifetime="process"`, drained by `end_process`; it is a
        declaration-side tier and not one of the values here.

        Optional. Defaults to 'each' if not implemented.
        """
        ...

    @property
    def autouse(self) -> bool:
        """If True, this fixture runs for every test without explicit annotation.

        Optional. Defaults to False if not implemented.
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

    def wrap(self, *, test_fn: Any, marker_args: dict[str, Any]) -> Any:
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

    def collect(self, *, path: str, module: object) -> list[CollectedItem]:
        """Collect test items from an already-imported module.

        Args:
            path: Absolute filesystem path to the test file.
            module: The imported module object.

        Returns:
            A list of `CollectedItem` objects describing the tests found.
            Return an empty list if the collector finds nothing.

        """
        ...


@runtime_checkable
class Reporter(Protocol):
    """Protocol for plugin reporters."""

    def test_started(self, *, item: CollectedItem) -> None:
        """Called immediately before a test begins executing.

        Args:
            item: The `CollectedItem` identifying the test
                (fn_name, lineno, markers, param_id, etc.).

        """
        ...

    def test_completed(
        self, *, item: CollectedItem, outcome: TestResult, duration_ms: float
    ) -> None:
        """Called immediately after a test finishes, whether it passed or failed.

        Args:
            item: The same `CollectedItem` passed to `test_started`.
            outcome: A `TestResult` with the test status and any failure
                information.
            duration_ms: Wall-clock time the test took, in milliseconds.

        """
        ...

    def finish(self, *, collect_errors: list[Any], interrupted: bool) -> None:
        """Run once after all tests have completed and teardown is done.

        Args:
            collect_errors: List of errors encountered during collection
                (module import failures, etc.).  Empty when collection
                succeeded cleanly.
            interrupted: `True` if the run was cut short (e.g. ``--maxfail``
                threshold reached or a signal was received).

        """
        ...


@runtime_checkable
class CoverageProvider(Protocol):
    """Protocol for coverage collection backends.

    The built-in provider uses coverage.py. Plugins can override this
    to use alternative coverage tools (e.g. slipcover). At most one
    provider is allowed across all plugins — registering more than one
    is a conflict raised at plugin resolution as
    :class:`ConflictingCoverageError`.

    See Also:
        - :attr:`oxitest.Plugin.coverage_provider` — how a plugin exposes
          an implementation to oxitest.
        - :class:`oxitest.CovReportFormat` — the report-format enum
          consumed by :meth:`report`.

    Examples:
        Any object with matching ``start``, ``stop``, and ``report``
        methods satisfies the protocol:

        >>> from oxitest import CoverageProvider, CovReportFormat
        >>> class MyProvider:
        ...     def start(self) -> None: pass
        ...     def stop(self) -> None: pass
        ...     def report(self, *, fmt: CovReportFormat) -> int: return 0
        >>> isinstance(MyProvider(), CoverageProvider)
        True
        >>> isinstance(object(), CoverageProvider)
        False

    """

    def start(self) -> None:
        """Begin coverage collection. Called before any test execution."""
        ...

    def stop(self) -> None:
        """Stop collection, save data, combine parallel data files."""
        ...

    def report(self, *, fmt: CovReportFormat) -> int:
        """Generate report in the given format. Returns 0 on success."""
        ...


@dataclass(frozen=True)
class Plugin:
    """Typed declaration of what a plugin provides.

    Returned by the plugin's ``oxitest_plugin()`` entry-point function.
    A ``Plugin`` bundles the hooks and backend implementations a plugin
    contributes to oxitest — lazy fixture hooks activated on first
    use, eager collector/reporter hooks activated at startup, and
    singleton backends for async runtime, debugger, and coverage.

    Backends default to null-object stand-ins. Discovery filters null
    objects by identity, so a plugin that omits a backend contributes
    nothing at that slot rather than shadowing a real registration
    from another plugin.

    See Also:
        - :class:`oxitest.AsyncBackend` — the async runtime backend
          protocol.
        - :class:`oxitest.DebuggerBackend` — the debugger backend
          protocol.
        - :class:`oxitest.CoverageProvider` — the coverage backend
          protocol.
        - :class:`oxitest.CliExtension` — sibling configuration
          mechanism, exposed via a separate module-level attribute
          (``oxitest_cli_extension``).

    Examples:
        A minimal empty plugin contributes nothing:

        >>> from oxitest import Plugin
        >>> plugin = Plugin()
        >>> plugin.fixture_providers
        ()
        >>> plugin.reporters
        ()

        Fields are declared by keyword — construct with just the hooks
        the plugin provides:

        >>> class MyReporter:
        ...     pass
        >>> plugin = Plugin(reporters=(MyReporter(),))
        >>> len(plugin.reporters)
        1

    """

    # Fixture-adjacent hooks (lazy — activated on first use)
    log_backends: tuple[LogBackend, ...] = ()
    fixture_providers: tuple[FixtureProvider, ...] = ()
    execution_wrappers: tuple[ExecutionWrapper, ...] = ()

    # Global hooks (eager — activated at startup)
    collectors: tuple[Collector, ...] = ()
    reporters: tuple[Reporter, ...] = ()

    # Async runtime backend (at most one across all plugins; null-object default)
    async_backend: AsyncBackend = _NULL_ASYNC_BACKEND

    # Debugger backend (at most one across all plugins; null-object default)
    debugger_backend: DebuggerBackend = _NULL_DEBUGGER

    # Coverage backend (at most one across all plugins; null-object default)
    coverage_provider: CoverageProvider = _NULL_COVERAGE


def skipped(*, message: str) -> SkippedResult:
    """Skipped result for an ExecutionWrapper when a test cannot run.

    See Also:
        - :class:`SkippedResult` — the returned variant.

    Examples:
        >>> from oxitest.plugin import skipped, SkippedResult
        >>> r = skipped(message="no network")
        >>> isinstance(r, SkippedResult)
        True
        >>> r.message
        'no network'

    """
    return SkippedResult(message=message)


def xfailed(*, message: str) -> XFailedResult:
    """XFailed result for an ExecutionWrapper when failure was expected.

    See Also:
        - :class:`XFailedResult` — the returned variant.

    Examples:
        >>> from oxitest.plugin import xfailed, XFailedResult
        >>> r = xfailed(message="known bug")
        >>> isinstance(r, XFailedResult)
        True
        >>> r.message
        'known bug'

    """
    return XFailedResult(message=message)


def warned(*, message: str) -> WarnedResult:
    """Warned result for an ExecutionWrapper with a warning-level outcome.

    ``no_message_lines`` deliberately not exposed; extend additively if
    needed.

    See Also:
        - :class:`WarnedResult` — the returned variant.

    Examples:
        >>> from oxitest.plugin import warned, WarnedResult
        >>> r = warned(message="deprecation notice")
        >>> isinstance(r, WarnedResult)
        True
        >>> r.message
        'deprecation notice'

    """
    return WarnedResult(message=message)
