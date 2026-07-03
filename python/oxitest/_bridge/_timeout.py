"""Cross-platform test timeout enforcement.

Unix/macOS: uses signal.alarm (SIGALRM) — main thread only, zero overhead.
Windows: uses ctypes.pythonapi.PyThreadState_SetAsyncExc via threading.Timer —
best-effort; fires at the next Python bytecode boundary after the deadline.
C extensions holding the GIL without yielding may delay the Windows interrupt.
"""

from __future__ import annotations

__all__ = ["OxitestTimeoutError", "extract_timeout_seconds", "make_timeout_wrapper"]

import ctypes
import signal
import threading
from collections.abc import Mapping
from typing import Any

from oxitest._bridge._errors import OxitestTimeoutError


class _UnixTimeoutContext:
    """Timeout context manager using SIGALRM (Unix/macOS only).

    Must be called from the main thread — signal.alarm and signal.signal
    raise ValueError when called from a non-main thread.
    """

    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._old_handler: Any = None

    def __enter__(self) -> None:
        self._old_handler = signal.signal(signal.SIGALRM, self._raise)
        signal.alarm(self._seconds)

    def __exit__(self, *_: object) -> None:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self._old_handler)

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
        self._thread_id: int = 0
        self._timer: threading.Timer | None = None

    def __enter__(self) -> None:
        self._thread_id = threading.get_ident()
        self._timer = threading.Timer(self._seconds, self._inject)
        self._timer.start()

    def __exit__(self, *_: object) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _inject(self) -> None:
        result = ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(self._thread_id),
            ctypes.py_object(OxitestTimeoutError),
        )
        # result == 0: thread not found (timeout silently failed)
        # result == 1: success
        # result > 1: set on multiple threads (should not happen)
        if result == 0:
            import warnings

            warnings.warn(
                "OxitestTimeoutError could not be injected: thread not found",
                RuntimeWarning,
                stacklevel=1,
            )
        elif result > 1:
            # Undo: we accidentally set the exception on multiple threads
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(self._thread_id),
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
            from oxitest._bridge.result import TimeoutResult

            return TimeoutResult(message=f"Timed out after {seconds}s")

    return wrapper
