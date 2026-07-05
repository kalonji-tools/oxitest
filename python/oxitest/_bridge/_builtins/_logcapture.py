from __future__ import annotations

__all__ = ["LogBackend", "LogCapture", "StdlibLogBackend", "_LogCaptureFixture"]

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from oxitest._bridge._builtins._base import BuiltinFixture

if TYPE_CHECKING:
    from oxitest._bridge._builtin_context import _BuiltinContext
from oxitest._bridge._fixture_type import injectable


@runtime_checkable
class LogBackend(Protocol):
    """Protocol for log-capture backends.

    Implement this to integrate a custom logging library with LogCapture.
    Each backend owns its install/uninstall lifecycle and produces stdlib
    `logging.LogRecord` objects so the unified API stays consistent.
    """

    def install(self) -> None: ...
    def uninstall(self) -> None: ...

    @property
    def records(self) -> list[logging.LogRecord]: ...


class _CapturingHandler(logging.Handler):
    """Private handler that buffers records in memory instead of emitting them.

    Records are accumulated in `_records` and accessed via the owning
    `StdlibLogBackend.records` property.
    """

    def __init__(self) -> None:
        super().__init__()
        self._records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self._records.append(record)


class StdlibLogBackend:
    """Captures records from Python's stdlib `logging` module.

    Attaches a handler to the root logger on `install()` and removes it on
    `uninstall()`. Produces `logging.LogRecord` objects directly.
    """

    def __init__(self, level: int = logging.WARNING) -> None:
        self._level = level
        self._handler = _CapturingHandler()
        self._handler.setLevel(level)
        self._old_level: int | None = None

    def install(self) -> None:
        if self._old_level is not None:
            return  # already installed
        root = logging.getLogger()
        self._old_level = root.level
        root.setLevel(self._level)
        root.addHandler(self._handler)

    def uninstall(self) -> None:
        root = logging.getLogger()
        root.removeHandler(self._handler)
        if self._old_level is not None:
            root.setLevel(self._old_level)
            self._old_level = None

    @property
    def records(self) -> list[logging.LogRecord]:
        return list(self._handler._records)

    def set_level(self, level: int, logger: str | None = None) -> None:
        """Set capture threshold on the named logger (None = root)."""
        target = logging.getLogger(logger)
        target.setLevel(level)
        self._handler.setLevel(level)
        if logger is None:
            self._level = level


@injectable
class LogCapture:
    """Aggregates log records from all registered backends.

    Constructed with a list of `LogBackend` instances. Each backend is
    installed immediately; all are uninstalled on `_teardown()`.
    Records from all backends are merged and sorted by creation time.
    """

    def __init__(self, backends: list[LogBackend]) -> None:
        self._backends = backends
        for b in backends:
            b.install()

    @property
    def records(self) -> list[logging.LogRecord]:
        """All log records from all backends, sorted by creation time."""
        all_records: list[logging.LogRecord] = []
        for b in self._backends:
            all_records.extend(b.records)
        return sorted(all_records, key=lambda r: r.created)

    @property
    def text(self) -> str:
        """Formatted log output — one `'LEVEL    message'` line per record."""
        joined = "\n".join(f"{r.levelname:<8} {r.getMessage()}" for r in self.records)
        return joined + "\n" if joined else ""

    def set_level(self, level: int, logger: str | None = None) -> None:
        """Set the minimum capture level, filtering out records below *level*.

        Args:
            level: Logging level integer (e.g. `logging.DEBUG`, `logging.WARNING`).
            logger: Name of the logger to configure. `None` applies the level
                to the root logger.
        """
        for b in self._backends:
            if isinstance(b, StdlibLogBackend):
                b.set_level(level, logger)

    @contextmanager
    def at_level(
        self, level: int, logger: str | None = None
    ) -> Generator[None, None, None]:
        """Context manager that temporarily sets the log level then restores it.

        Args:
            level: Logging level integer (e.g. `logging.DEBUG`).
            logger: Logger name, or `None` for the root logger.

        Example:
            ```python
            import logging

            def test_debug_messages(log: LogCapture) -> None:
                with log.at_level(logging.DEBUG):
                    run_thing()
                assert "debug detail" in log.text
            ```
        """
        target = logging.getLogger(logger)
        old_level = target.level
        self.set_level(level, logger)
        try:
            yield
        finally:
            self.set_level(old_level, logger)

    def _teardown(self) -> None:
        for b in reversed(self._backends):
            b.uninstall()


class _LogCaptureFixture(BuiltinFixture, fixture_type=LogCapture):
    def create(self, ctx: _BuiltinContext) -> LogCapture:
        backends: list[LogBackend] = [StdlibLogBackend()]
        if ctx.plugin_registry is not None:
            backends.extend(ctx.plugin_registry.log_backends)
        cap = LogCapture(backends)
        ctx.teardown_stack.append(cap._teardown)
        return cap
