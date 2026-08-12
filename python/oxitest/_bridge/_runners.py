"""Test runner functions for sync and async execution.

Extracted from ``executor.py`` and ``_middleware.py`` to create a clean
dependency chain: ``_diagnostics`` <- ``_runners`` <- ``_middleware`` <- ``executor``.
"""

from __future__ import annotations

__all__ = [
    "NO_DEBUG",
    "DebugContext",
    "DebugMode",
    "run_base",
    "run_base_async",
]

import contextlib
import inspect
import sys as _sys
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from oxitest._bridge._builtins._capture import _CaptureBase
from oxitest._bridge._debugger import _NULL_DEBUGGER, DebuggerBackend
from oxitest._bridge._diagnostics import (
    check_warnings,
    dispatch_exception,
    is_debuggable,
)
from oxitest._bridge._errors import TestReturnedValueError
from oxitest._bridge._timeout import OxitestTimeoutError
from oxitest._bridge.result import PassedResult, WarnedResult
from oxitest._oxitest import trace as _rust_trace

if TYPE_CHECKING:
    from oxitest._bridge.result import TestResult


class DebugMode(StrEnum):
    """Debug mode passed from Rust via the bridge.

    ``OFF`` is the default; reifies "no debug" as a sentinel value so
    ``DebugContext.mode`` can be non-Optional (ADR-0007 Rule 4 option 1).

    StrEnum values match the Rust ``DebugMode::as_str()`` output so
    PyO3 string extraction works without custom glue.
    """

    OFF = "off"
    POST_MORTEM = "post-mortem"
    ALWAYS = "always"


@dataclass(frozen=True, slots=True)
class DebugContext:
    """Debug/trace and diagnostic display configuration."""

    mode: DebugMode = DebugMode.OFF
    node_id: str = ""
    backend: DebuggerBackend = _NULL_DEBUGGER
    file: Any = None
    show_locals: bool = False
    show_internals: bool = False

    def __post_init__(self) -> None:
        # Rust bridge passes ``mode`` as a raw string; coerce so identity
        # checks (`is DebugMode.ALWAYS`) hold instead of only equality.
        if not isinstance(self.mode, DebugMode):
            object.__setattr__(self, "mode", DebugMode(self.mode))


NO_DEBUG = DebugContext()


def _suspend_capture(all_kwargs: dict[str, Any]) -> None:
    """Restore real stdout/stderr by suspending any active capture fixtures."""
    for v in all_kwargs.values():
        if isinstance(v, _CaptureBase):
            v.close()


def _print_banner(
    mode: str,
    node_id: str,
    *body_lines: str,
    file: Any = None,
) -> None:
    """Print a debug/trace banner with optional body lines."""
    width = max(60, len(node_id) + 12)
    header = f"── {mode} {node_id} "
    header += "─" * (width - len(header))
    # Use "warn" level so banners are visible with the default tracing
    # filter ("warn").  Only fires during explicit --debug sessions.
    for text in (header, *body_lines):
        if file is not None:
            print(text, file=file)
        else:
            _rust_trace("warn", __name__, text)


def _trace_before_test(
    all_kwargs: dict[str, Any],
    node_id: str,
    backend: DebuggerBackend,
    *,
    file: Any = None,
) -> None:
    """Suspend capture, print trace banner, call backend.trace(), restore."""
    managers = [
        v.disabled() for v in all_kwargs.values() if isinstance(v, _CaptureBase)
    ]

    with contextlib.ExitStack() as stack:
        for mgr in managers:
            stack.enter_context(mgr)
        _print_banner(
            "TRACE",
            node_id,
            "Stepping into test (type 'c' to run, 'q' to quit)",
            file=file,
        )
        backend.trace()


def _debug_post_mortem(
    all_kwargs: dict[str, Any],
    node_id: str,
    exc: BaseException,
    backend: DebuggerBackend,
    *,
    file: Any = None,
) -> None:
    """Permanently suspend capture, print debug banner, call backend.post_mortem()."""
    _suspend_capture(all_kwargs)
    _print_banner(
        "DEBUG",
        node_id,
        f"{type(exc).__name__}: {exc}",
        "Entering debugger (type 'h' for help, 'q' to quit)",
        file=file,
    )
    *_, tb = _sys.exc_info()
    if tb:
        backend.post_mortem(tb)


def _refuse_generator_result(fn: Callable[..., Any], returned: object) -> None:
    """Refuse a call that returned a generator instead of running (#2067).

    Value-side, so a ``functools.wraps`` wrapper cannot hide the shape:
    ``isgeneratorfunction`` answers False on the wrapper while the call still
    returns a generator. That case is why this point exists — the collection
    guard in ``importer.py`` catches every shape a static answer can see.

    Generators only, **not** ``returned is not None``. A non-None return ran
    its body, so it is a smell rather than the silent defect, and the
    ``test-returns-value`` strict check owns it.

    Both predicates, for the reason the collection guard uses both: an async
    generator reaches this *sync* runner, because ``is_async`` is derived from
    ``iscoroutinefunction``, which answers False for one.

    ``TestReturnedValueError`` is enrolled in ``_USAGE_ERROR_TYPES``, so
    ``_handle_runtime_exception`` sets ``usage_error`` and the run exits 4
    without being stopped — every sibling test still reports (#1761).
    """
    if not (inspect.isgenerator(returned) or inspect.isasyncgen(returned)):
        return
    name = getattr(fn, "__name__", "<test>")
    # The message must not name a wrapper as the cause. A wrapped generator is
    # one way to reach this point; an ordinary `async def` that returns a
    # generator is another, and it has no wrapper at all. Naming the wrapper
    # sent that second reader looking for a decorator that is not there.
    msg = (
        f"{name} returned a {type(returned).__name__} instead of None, so none "
        f"of its body ran and the test proved nothing.\n"
        f"Only the returned value shows this shape, so collection could not "
        f"refuse it. A test function returns None.\n"
        f"Hint: remove the yield, or return nothing. To express setup and "
        f"teardown, move the generator into a fixture, where yield is how "
        f"teardown is written."
    )
    raise TestReturnedValueError(msg)


def _call_with_warnings(
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    no_message_lines: tuple[int, ...],
) -> TestResult:
    """Shared warning-capture logic for the test happy path.

    Calls *fn* synchronously and checks for warnings.
    Exceptions propagate to the caller for handling.
    """
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        returned = fn(**all_kwargs)
    # Outside the `with`, to keep the block to the one statement it guards.
    # Position is not load-bearing beyond that: the refusal raises before
    # `check_warnings` either way, and a call that returns a generator ran no
    # part of the body, so there is never a warning to lose.
    _refuse_generator_result(fn, returned)
    has_warnings, warning_msg = check_warnings(w, all_kwargs)
    if has_warnings:
        return WarnedResult(message=warning_msg, no_message_lines=no_message_lines)
    return PassedResult(no_message_lines=no_message_lines)


def run_base(
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    no_message_lines: tuple[int, ...],
    *,
    debug: DebugContext = NO_DEBUG,
) -> TestResult:
    """Run the test function and map exceptions to TestResult."""
    if debug.mode is DebugMode.ALWAYS:
        _trace_before_test(all_kwargs, debug.node_id, debug.backend, file=debug.file)
    try:
        return _call_with_warnings(fn, all_kwargs, no_message_lines)
    except OxitestTimeoutError:
        raise  # propagate to timeout wrapper
    except BaseException as exc:
        if debug.mode in (DebugMode.POST_MORTEM, DebugMode.ALWAYS) and is_debuggable(
            exc
        ):
            _debug_post_mortem(
                all_kwargs, debug.node_id, exc, debug.backend, file=debug.file
            )
        result = dispatch_exception(
            exc,
            show_locals=debug.show_locals,
            show_internals=debug.show_internals,
        )
        if result is not None:
            return result
        raise


async def run_base_async(
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    no_message_lines: tuple[int, ...],
) -> TestResult:
    """Run an async test function and map exceptions to TestResult."""
    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            returned = await fn(**all_kwargs)
        _refuse_generator_result(fn, returned)
        has_warnings, warning_msg = check_warnings(w, all_kwargs)
        if has_warnings:
            return WarnedResult(message=warning_msg, no_message_lines=no_message_lines)
        return PassedResult(no_message_lines=no_message_lines)
    except OxitestTimeoutError:
        raise  # propagate to timeout wrapper
    except BaseException as exc:
        result = dispatch_exception(exc)
        if result is not None:
            return result
        raise
