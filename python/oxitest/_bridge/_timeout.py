"""Cross-platform test timeout enforcement.

Unix/macOS: uses signal.alarm (SIGALRM) — main thread only, zero overhead.
Windows: uses ctypes.pythonapi.PyThreadState_SetAsyncExc via threading.Timer —
best-effort; fires at the next Python bytecode boundary after the deadline.
C extensions holding the GIL without yielding may delay the Windows interrupt.
"""

from __future__ import annotations

import ctypes
import signal
import threading
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
    def _raise(signum: int, frame: object) -> None:
        raise OxitestTimeoutError()


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
