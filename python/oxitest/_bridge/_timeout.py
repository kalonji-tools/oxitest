"""Cross-platform test timeout enforcement.

Unix/macOS: uses signal.alarm (SIGALRM) — main thread only, zero overhead.
Windows: uses ctypes.pythonapi.PyThreadState_SetAsyncExc via threading.Timer —
best-effort; fires at the next Python bytecode boundary after the deadline.
C extensions holding the GIL without yielding may delay the Windows interrupt.
"""

from __future__ import annotations

__all__ = [
    "OxitestTimeoutError",
    "Timeout",
    "TimeoutOff",
    "TimeoutSet",
    "_ActiveHandler",
    "_ActiveTimer",
    "_IdleHandler",
    "_IdleTimer",
    "extract_timeout_seconds",
    "make_timeout_wrapper",
    "parse_timeout",
]

import ctypes
import signal
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import OxitestTimeoutError
from oxitest._bridge.result import DiagnosticSeverity, TimeoutResult


@dataclass(frozen=True, slots=True)
class TimeoutOff:
    """Timeout absent — no wrapper applied."""


@dataclass(frozen=True, slots=True)
class TimeoutSet:
    """Timeout set to `seconds`. `seconds=0` fires immediately (documents behavior)."""

    seconds: int


Timeout = TimeoutOff | TimeoutSet


def parse_timeout(value: int | None) -> Timeout:
    """Config-boundary conversion: None → TimeoutOff, int → TimeoutSet."""
    if value is None:
        return TimeoutOff()
    return TimeoutSet(value)


# ── State variants for context-manager lifecycles (ADR-0007 Rule 4) ───────────


@dataclass(frozen=True, slots=True)
class _IdleHandler:
    pass


@dataclass(frozen=True, slots=True)
class _ActiveHandler:
    old_handler: Any


_UnixHandlerState = _IdleHandler | _ActiveHandler


@dataclass(frozen=True, slots=True)
class _IdleTimer:
    pass


@dataclass(frozen=True, slots=True)
class _ActiveTimer:
    timer: threading.Timer


_WindowsTimerState = _IdleTimer | _ActiveTimer


class _UnixTimeoutContext:
    """Timeout context manager using SIGALRM (Unix/macOS only).

    Must be called from the main thread — signal.alarm and signal.signal
    raise ValueError when called from a non-main thread.
    """

    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._state: _UnixHandlerState = _IdleHandler()

    def __enter__(self) -> None:
        old = signal.signal(signal.SIGALRM, self._raise)
        self._state = _ActiveHandler(old_handler=old)
        signal.alarm(self._seconds)

    def __exit__(self, *_: object) -> None:
        signal.alarm(0)
        if isinstance(self._state, _ActiveHandler):
            signal.signal(signal.SIGALRM, self._state.old_handler)
            self._state = _IdleHandler()

    @staticmethod
    def _raise(_signum: int, _frame: object) -> None:
        raise OxitestTimeoutError


class _WindowsTimeoutContext:
    """Best-effort timeout via ctypes async exception injection.

    Fires at the next Python bytecode boundary after the deadline.
    Pure Python code is always interrupted promptly. C extensions
    holding the GIL without yielding may delay the interrupt.
    """

    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._state: _WindowsTimerState = _IdleTimer()

    def __enter__(self) -> None:
        thread_id = threading.get_ident()
        timer = threading.Timer(self._seconds, lambda: self._inject(thread_id))
        self._state = _ActiveTimer(timer=timer)
        timer.start()

    def __exit__(self, *_: object) -> None:
        if isinstance(self._state, _ActiveTimer):
            self._state.timer.cancel()
            self._state = _IdleTimer()

    def _inject(self, thread_id: int) -> None:
        result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread_id),
            ctypes.py_object(OxitestTimeoutError),
        )
        if result == 0:
            emit_diagnostic(
                DiagnosticSeverity.WARNING,
                "timeout",
                "OxitestTimeoutError could not be injected: thread not found",
            )
        elif result > 1:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(thread_id),
                None,
            )


def _timeout_context(seconds: int) -> _UnixTimeoutContext | _WindowsTimeoutContext:
    """Return a platform-appropriate timeout context manager."""
    if hasattr(signal, "alarm"):
        return _UnixTimeoutContext(seconds)
    return _WindowsTimeoutContext(seconds)


def extract_timeout_seconds(mark_kwargs: Mapping[str, object]) -> int:
    """Extract the ``seconds`` value from a timeout mark's kwargs.

    Validated as ``int > 0`` at mark creation time (``_TimeoutMark``).
    This accessor provides a typed return so callers don't need to
    re-validate or cast.
    """
    seconds = mark_kwargs["seconds"]
    if not isinstance(seconds, int):
        msg = f"timeout seconds must be int, got {type(seconds).__name__}"
        raise TypeError(msg)
    return seconds


def make_timeout_wrapper(seconds: int) -> Any:
    """Return an execution wrapper that enforces a timeout of *seconds*.

    The returned wrapper has the ExecutionWrapper signature:
    `wrapper(next_fn: Callable[[], TestResult]) -> TestResult`.
    """

    def wrapper(next_fn: Any) -> Any:
        try:
            with _timeout_context(seconds):
                return next_fn()
        except OxitestTimeoutError:
            return TimeoutResult(message=f"Timed out after {seconds}s")

    return wrapper
