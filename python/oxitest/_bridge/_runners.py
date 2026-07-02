"""Test runner functions for sync and async execution.

Extracted from ``executor.py`` and ``_middleware.py`` to create a clean
dependency chain: ``_diagnostics`` <- ``_runners`` <- ``_middleware`` <- ``executor``.
"""

from __future__ import annotations

__all__ = [
    "DebugMode",
    "run_base",
    "run_base_async",
]

import contextlib
import warnings
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from oxitest._bridge._diagnostics import (
    check_warnings,
    dispatch_exception,
    is_debuggable,
)
from oxitest._bridge._timeout import OxitestTimeoutError
from oxitest._bridge.result import PassedResult, TestResult, WarnedResult

if TYPE_CHECKING:
    from oxitest._bridge._debugger import DebuggerBackend


class DebugMode(StrEnum):
    """Debug mode passed from Rust via the bridge.

    StrEnum values match the Rust ``DebugMode::as_str()`` output so
    PyO3 string extraction works without custom glue.
    """

    POST_MORTEM = "post-mortem"
    ALWAYS = "always"


def _suspend_capture(all_kwargs: dict[str, Any]) -> None:
    """Restore real stdout/stderr by suspending any active capture fixtures."""
    from oxitest._bridge._builtins._capture import _CaptureBase

    for v in all_kwargs.values():
        if isinstance(v, _CaptureBase):
            v._restore()


def _print_banner(
    mode: str,
    node_id: str,
    *body_lines: str,
    file: Any = None,
) -> None:
    """Print a debug/trace banner with optional body lines."""
    import sys as _sys

    out = file if file is not None else _sys.__stderr__
    width = max(60, len(node_id) + 12)
    header = f"── {mode} {node_id} "
    header += "─" * (width - len(header))
    print(header, file=out)
    for line in body_lines:
        print(line, file=out)


def _trace_before_test(
    all_kwargs: dict[str, Any],
    node_id: str,
    backend: DebuggerBackend,
    *,
    file: Any = None,
) -> None:
    """Suspend capture, print trace banner, call backend.trace(), restore."""
    from oxitest._bridge._builtins._capture import _CaptureBase

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
    tb = exc.__traceback__
    assert tb is not None  # guaranteed inside except block
    backend.post_mortem(tb)


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
        fn(**all_kwargs)
    has_warnings, warning_msg = check_warnings(w, all_kwargs)
    if has_warnings:
        return WarnedResult(message=warning_msg, no_message_lines=no_message_lines)
    return PassedResult(no_message_lines=no_message_lines)


def run_base(
    fn: Callable[..., Any],
    all_kwargs: dict[str, Any],
    no_message_lines: tuple[int, ...],
    *,
    debug_mode: str | None = None,
    node_id: str = "",
    backend: DebuggerBackend | None = None,
    show_locals: bool = False,
    show_internals: bool = False,
    file: Any = None,
) -> TestResult:
    """Run the test function and map exceptions to TestResult."""
    if debug_mode == DebugMode.ALWAYS and backend is not None:
        _trace_before_test(all_kwargs, node_id, backend, file=file)
    try:
        return _call_with_warnings(fn, all_kwargs, no_message_lines)
    except OxitestTimeoutError:
        raise  # propagate to timeout wrapper
    except BaseException as exc:
        if (
            debug_mode in (DebugMode.POST_MORTEM, DebugMode.ALWAYS)
            and backend is not None
            and is_debuggable(exc)
        ):
            _debug_post_mortem(all_kwargs, node_id, exc, backend, file=file)
        result = dispatch_exception(
            exc, show_internals=show_internals, show_locals=show_locals
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
            await fn(**all_kwargs)
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
