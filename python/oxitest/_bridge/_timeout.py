"""Cross-platform test timeout enforcement.

Unix/macOS: uses signal.setitimer (ITIMER_REAL/SIGALRM) — main thread only,
zero overhead. The process has exactly one ITIMER_REAL slot, so this arm does
not own its timer exclusively; it saves and restores what it found (#2001).
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
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import OxitestTimeoutError
from oxitest._bridge.result import (
    DiagnosticSeverity,
    StatusKind,
    TimeoutResult,
    WarnedResult,
)


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
    #: Seconds left on the enclosing deadline when this context armed, or 0.0
    #: when nothing was pending. Restoring it is what makes the context nestable.
    enclosing_remaining: float
    #: What was actually armed — the requested value, or the enclosing remaining
    #: when that was tighter.
    effective: float
    entered_at: float


_UnixHandlerState = _IdleHandler | _ActiveHandler

#: Slack between the ``getitimer`` read and the ``monotonic`` read in
#: ``__exit__``. Both measure the same wall-clock interval, so a run nobody
#: interfered with reports zero drift; this absorbs the gap between two
#: syscalls only, and is not a tuned tolerance (#2001).
_READ_SKEW_SECONDS = 0.25

#: Re-armed instead of 0 when the enclosing deadline has already expired.
#: ``setitimer(0)`` means *cancel*, which would silently drop an expired
#: enclosing deadline instead of letting it fire.
_MIN_REARM_SECONDS = 0.000001


@dataclass(frozen=True, slots=True)
class _IdleTimer:
    pass


@dataclass(frozen=True, slots=True)
class _ActiveTimer:
    timer: threading.Timer


_WindowsTimerState = _IdleTimer | _ActiveTimer


class _UnixTimeoutContext:
    """Timeout context manager using ITIMER_REAL/SIGALRM (Unix/macOS only).

    Must be called from the main thread — signal.setitimer and signal.signal
    raise ValueError when called from a non-main thread.

    The process has exactly one ITIMER_REAL slot, so this context does not own
    it exclusively. It saves the enclosing deadline on entry and restores it on
    exit, caps its own deadline at whatever the enclosing one has left, and
    reports when something else wrote the slot while it was held (#2001).
    """

    #: SIGALRM interrupts a blocking C call: measured, ``time.sleep(5)`` under a
    #: 1s alarm raises at 1.00s. An async test therefore still benefits from
    #: this arm, because ``asyncio.wait_for`` cannot bound a coroutine that
    #: blocks — it never yields, so the loop never delivers the cancellation.
    bounds_blocking_calls = True

    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._state: _UnixHandlerState = _IdleHandler()
        self._effective_seconds: float = float(seconds)
        self._deadline_taken = False

    @property
    def effective_seconds(self) -> float:
        """The deadline actually armed, which the enclosing one may have capped."""
        return self._effective_seconds

    @property
    def deadline_taken(self) -> bool:
        """True when something else wrote the timer while this context held it."""
        return self._deadline_taken

    def __enter__(self) -> None:
        old = signal.signal(signal.SIGALRM, self._raise)
        enclosing = signal.getitimer(signal.ITIMER_REAL)[0]
        effective = float(self._seconds)
        if 0.0 < enclosing < effective:
            effective = enclosing
        signal.setitimer(signal.ITIMER_REAL, effective)
        self._effective_seconds = effective
        self._deadline_taken = False
        self._state = _ActiveHandler(
            old_handler=old,
            enclosing_remaining=enclosing,
            effective=effective,
            entered_at=time.monotonic(),
        )

    def __exit__(self, exc_type: object = None, *_: object) -> None:
        state = self._state
        if not isinstance(state, _ActiveHandler):
            return
        remaining = signal.getitimer(signal.ITIMER_REAL)[0]
        elapsed = time.monotonic() - state.entered_at
        # The in-flight exception is the honest signal that the deadline fired.
        # A deadline that fired and one that was cancelled both leave the slot
        # at 0.0, so arithmetic alone cannot tell them apart (#2001).
        fired = exc_type is OxitestTimeoutError
        predicted = max(state.effective - elapsed, 0.0)
        self._deadline_taken = (
            not fired and abs(remaining - predicted) > _READ_SKEW_SECONDS
        )
        # A capped deadline can only fire when the enclosing one is also spent,
        # so this often re-arms a timer that fires within microseconds —
        # measured at 0.010 ms for _MIN_REARM_SECONDS. That fire lands while
        # this context's own OxitestTimeoutError is still propagating, and the
        # wrapper's `except` cannot tell the two apart because both are the
        # same exception type. The enclosing test therefore reports passed
        # rather than timing out, in the one case where its deadline expires
        # *during* a nested run. Restoring the handler before arming does not
        # change it — measured, 8/8 either way — because the attribution is
        # lost in the wrapper, not at the handler. Known limit, recorded in
        # ADR-0016; a deadline with time left after the nested run is
        # unaffected and fires normally (#2001).
        if state.enclosing_remaining > 0.0:
            rest = state.enclosing_remaining - elapsed
            signal.setitimer(signal.ITIMER_REAL, max(rest, _MIN_REARM_SECONDS))
        else:
            signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, state.old_handler)
        self._state = _IdleHandler()

    @staticmethod
    def _raise(_signum: int, _frame: object) -> None:
        raise OxitestTimeoutError


def _set_async_exc(thread_id: int, exc: type[BaseException] | None) -> int:
    """Set or clear a pending async exception on *thread_id*.

    ``exc=None`` clears whatever is pending. Both directions route through here
    so the guard in ``_WindowsTimeoutContext`` and its over-broad-result branch
    cannot drift apart.
    """
    return int(
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_ulong(thread_id),
            ctypes.py_object(exc) if exc is not None else None,
        )
    )


class _WindowsTimeoutContext:
    """Best-effort timeout via ctypes async exception injection.

    Fires at the next Python bytecode boundary after the deadline.
    Pure Python code is always interrupted promptly. C extensions
    holding the GIL without yielding may delay the interrupt.
    """

    #: ``PyThreadState_SetAsyncExc`` fires only at a Python bytecode boundary,
    #: so a blocking C call is not bounded — measured at 5.00s under a 1s limit
    #: (#1989). An async test gains nothing from this arm and pays the
    #: post-``__exit__`` injection race, so it is not armed for one (#1998).
    bounds_blocking_calls = False

    def __init__(self, seconds: int) -> None:
        self._seconds = seconds
        self._effective_seconds: float = float(seconds)
        self._state: _WindowsTimerState = _IdleTimer()
        # Guards the state transition against the timer thread. Timer.cancel()
        # is only `self.finished.set()`, and Timer.run() checks `is_set()`
        # *before* calling the function — so a cancel arriving after that check
        # does nothing, and both sides can otherwise believe they won (#1998).
        self._lock = threading.Lock()
        self._injected = False
        # The thread the timer is aimed at, captured once in __enter__. __exit__
        # must clear *that* thread, not whichever one happens to run it — the
        # two are the same under `with`, but deriving them independently means a
        # future divergence would silently clear the wrong thread and restore
        # the very leak this guard exists to prevent.
        self._armed_thread_id: int | None = None

    @property
    def effective_seconds(self) -> float:
        """The deadline actually armed. Never capped — see ``deadline_taken``."""
        return self._effective_seconds

    @property
    def deadline_taken(self) -> bool:
        """Always False: a per-instance timer has no shared slot to lose (#2001).

        Nesting on this arm runs two independent timers and the first to fire
        wins, which reaches the same ``min`` invariant the Unix arm reaches by
        capping. There is no process-global slot for other code to write, so
        there is nothing to detect.
        """
        return False

    def __enter__(self) -> None:
        thread_id = threading.get_ident()
        timer = threading.Timer(self._seconds, lambda: self._inject(thread_id))
        with self._lock:
            self._state = _ActiveTimer(timer=timer)
            self._injected = False
            self._armed_thread_id = thread_id
        timer.start()

    def __exit__(self, exc_type: object = None, *_: object) -> None:
        # `exc_type` is unread here and named only to keep one __exit__ signature
        # across both arms: `_TimeoutContext` is a union the wrapper dispatches
        # over, so a divergent signature is a Liskov violation ty refuses. The
        # Unix arm reads it to tell a fired deadline from a cancelled one; this
        # arm has no shared slot, so it has nothing to tell apart (#2001).
        del exc_type
        with self._lock:
            if isinstance(self._state, _ActiveTimer):
                self._state.timer.cancel()
                self._state = _IdleTimer()
            injected = self._injected
            armed_thread_id = self._armed_thread_id
            self._injected = False
        if injected and armed_thread_id is not None:
            # SetAsyncExc only makes the exception *pending*; it is delivered at
            # the target thread's next bytecode boundary, which may be long
            # after this region. Clearing is the only way to stop an injection
            # we already lost the race to from landing on unrelated code.
            self._clear(armed_thread_id)

    def _clear(self, thread_id: int) -> None:
        """Drop any pending injection on *thread_id*."""
        _set_async_exc(thread_id, None)

    def _inject(self, thread_id: int, set_exc: Any = _set_async_exc) -> None:
        with self._lock:
            if isinstance(self._state, _IdleTimer):
                # __exit__ already ran: the region this timer guarded is over,
                # so injecting now would surface on whatever runs next.
                return
            self._injected = True
            result = set_exc(thread_id, OxitestTimeoutError)
        # Deliberately outside the lock: emit_diagnostic reaches user-visible
        # collection machinery, and holding a lock the timer thread also takes
        # while calling into it is a deadlock shape.
        if result == 0:
            emit_diagnostic(
                DiagnosticSeverity.WARNING,
                "timeout",
                "OxitestTimeoutError could not be injected: thread not found",
            )
        elif result > 1:
            self._clear(thread_id)


_TimeoutContext = _UnixTimeoutContext | _WindowsTimeoutContext


def _timeout_context_class() -> type[_TimeoutContext]:
    """Return the platform-appropriate timeout context *class*."""
    if hasattr(signal, "alarm"):
        return _UnixTimeoutContext
    return _WindowsTimeoutContext


def _timeout_context(seconds: int) -> _TimeoutContext:
    """Return a platform-appropriate timeout context manager."""
    return _timeout_context_class()(seconds)


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


def _timeout_message(effective: float, requested: int) -> str:
    """Name the deadline that fired, and say when it is not the one requested.

    A capped deadline reports the limit it enforced. Reporting the requested
    value would send the developer debugging against a number that was never
    armed — measured before the fix as ``Timed out after 60s`` for a deadline
    armed at ~1s (#2001).
    """
    if effective >= requested:
        # Byte-identical to the pre-#2001 message. The uncapped case is every
        # ordinary test, and every existing report and assertion reads this form.
        return f"Timed out after {requested}s"
    return (
        f"Timed out after {effective:g}s"
        f" (the requested {requested}s was capped by an enclosing deadline)"
    )


def make_timeout_wrapper(
    seconds: int,
    *,
    is_async: bool = False,
    context_cls: type[_TimeoutContext] | None = None,
) -> Any:
    """Return an execution wrapper that enforces a timeout of *seconds*.

    The returned wrapper has the ExecutionWrapper signature:
    `wrapper(next_fn: Callable[[], TestResult]) -> TestResult`.

    An async test already has its deadline enforced by ``asyncio.wait_for``.
    The OS-level arm is layered on top only when it bounds something the loop
    cannot see — a blocking call. Where it cannot, arming it buys nothing and
    costs the post-``__exit__`` injection race (#1998), so it is skipped.

    ``context_cls`` exists for tests: it makes both arms reachable from one
    platform, which is the only way either branch gets covered by CI.
    """
    cls = context_cls if context_cls is not None else _timeout_context_class()
    arm = not (is_async and not cls.bounds_blocking_calls)

    def wrapper(next_fn: Any) -> Any:
        # Bound before the `with` rather than as `with cls(seconds) as ctx`, so
        # __enter__ keeps returning None on both arms. The instance is read
        # afterwards for the deadline it actually armed (#2001).
        ctx = cls(seconds)
        try:
            if not arm:
                # No OS-level arm: the event loop owns the deadline. The
                # `except` below is still required — `_run_with_timeout` raises
                # OxitestTimeoutError out of `asyncio.wait_for`, and this is the
                # only place that turns it into a TimeoutResult. Returning a
                # bare passthrough here let that error escape uncaught, which is
                # invisible on any platform whose arm keeps the full wrapper.
                return next_fn()
            with ctx:
                result = next_fn()
        except OxitestTimeoutError:
            return TimeoutResult(
                message=_timeout_message(ctx.effective_seconds, seconds)
            )
        if ctx.deadline_taken and result.status is StatusKind.PASSED:
            # Only a pass is rewritten. oxitest did not observe a failure — it
            # observed that it could not observe — so the outcome states that
            # rather than manufacturing a failure from an absence. A result that
            # already carries its own message keeps it (#2001).
            return WarnedResult(
                message=(
                    f"the {seconds}s deadline was cleared during this test,"
                    " so the test did not run under a deadline"
                )
            )
        return result

    return wrapper
