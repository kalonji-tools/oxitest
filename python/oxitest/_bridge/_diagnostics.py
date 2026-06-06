"""Diagnostic helpers for exception → TestResult mapping and warning checks.

Extracted from ``_middleware.py`` to isolate pure diagnostic logic from the
middleware pipeline.  The three public functions — ``dispatch_exception``,
``check_warnings``, and ``is_debuggable`` — are consumed by ``_runners.py``
and ``executor.py``.
"""

from __future__ import annotations

__all__ = [
    "check_warnings",
    "dispatch_exception",
    "is_debuggable",
]

import linecache
import reprlib
import warnings
from typing import Any, cast

from oxitest._bridge._assert_error import (
    _OXITEST_NO_RHS,
    _OxitestAssertionError,
)
from oxitest._bridge._builtins._warncapture import _WarnCapture
from oxitest._bridge._fixture_context import FixtureTeardownWarning
from oxitest._bridge.result import Frame, StatusKind, TestResult

_REPR_MAX = 80


def _safe_repr(obj: object, max_len: int = _REPR_MAX) -> str:
    """Repr with length cap and failure safety via reprlib."""
    r = reprlib.Repr()
    r.maxstring = max_len
    r.maxother = max_len
    try:
        return r.repr(obj)
    except Exception:  # noqa: BLE001
        return "<repr failed>"


#: Backward-compatible alias used by assertion error rendering (80-char cap).
_repr_safe = _safe_repr

INTERNAL_PREFIXES = ("oxitest/_bridge/", "oxitest/_builtins/", "oxitest/plugin")


def _capture_locals(frame: Any) -> tuple[tuple[str, str], ...]:
    """Capture non-private local variables from a frame object."""
    return tuple(
        (k, _safe_repr(v, max_len=200))
        for k, v in frame.f_locals.items()
        if not k.startswith("_")
    )


def _get_location(exc: BaseException) -> tuple[str, int, str]:
    """Extract (file, lineno, source_line) from the innermost traceback frame."""
    tb = exc.__traceback__
    if tb is None:
        return ("", 0, "")
    while tb.tb_next is not None:
        tb = tb.tb_next
    file = tb.tb_frame.f_code.co_filename
    lineno = tb.tb_lineno
    source_line = linecache.getline(file, lineno).strip()
    return (file, lineno, source_line)


def _get_frames(
    exc: BaseException,
    *,
    show_internals: bool = False,
    show_locals: bool = False,
) -> tuple[Frame, ...]:
    """Extract structured traceback frames from an exception.

    Single-pass walk over the raw traceback chain.  When *show_internals*
    is False, frames from oxitest internals are filtered out (with a
    fallback to the last frame if all are internal).  When *show_locals*
    is True, each surviving frame includes captured local variables.
    """
    tb = exc.__traceback__
    frames: list[Frame] = []
    last_frame: Frame | None = None

    while tb is not None:
        code = tb.tb_frame.f_code
        filename = code.co_filename
        is_internal = any(p in filename for p in INTERNAL_PREFIXES)

        frame = Frame(
            file=filename,
            lineno=tb.tb_lineno,
            name=code.co_name,
            line=(linecache.getline(filename, tb.tb_lineno) or "").strip(),
            locals=_capture_locals(tb.tb_frame) if show_locals else (),
        )

        last_frame = frame
        if show_internals or not is_internal:
            frames.append(frame)

        tb = tb.tb_next

    # Fallback: if all frames were internal, keep the last one
    if not frames and last_frame is not None:
        frames.append(last_frame)

    return tuple(frames)


_FIELD_DIFF_SENTINEL = object()


def _compute_field_diffs(
    left: object,
    right: object,
) -> tuple[tuple[str, str, str], ...]:
    """Compute field-level diffs for dataclass instances.

    Returns tuple of (field_name, left_repr, right_repr) for differing fields.
    Returns empty tuple if not both dataclasses of the same type.
    """
    if type(left) is not type(right):
        return ()
    if not hasattr(left, "__dataclass_fields__"):
        return ()
    diffs: list[tuple[str, str, str]] = []
    dc_fields = cast(dict[str, Any], left.__dataclass_fields__)
    for name in dc_fields:
        lv = getattr(left, name, _FIELD_DIFF_SENTINEL)
        rv = getattr(right, name, _FIELD_DIFF_SENTINEL)
        if lv is _FIELD_DIFF_SENTINEL or rv is _FIELD_DIFF_SENTINEL:
            continue
        try:
            if lv != rv:
                diffs.append((name, _repr_safe(lv), _repr_safe(rv)))
        except Exception:  # noqa: BLE001
            continue
    return tuple(diffs)


def _handle_assertion_error(
    exc: AssertionError,
    *,
    show_internals: bool = False,
    show_locals: bool = False,
) -> TestResult:
    """Map an AssertionError to a failed TestResult."""
    file, lineno, source_line = _get_location(exc)
    if isinstance(exc, _OxitestAssertionError):
        msg = exc.args[0] if exc.args and exc.args[0] else ""
        left_repr = _repr_safe(exc.left)
        right_repr = _repr_safe(exc.right) if exc.right is not _OXITEST_NO_RHS else ""
        op = exc.op
        field_diffs = _compute_field_diffs(exc.left, exc.right) if op == "==" else ()
    else:
        msg = str(exc) if str(exc) else ""
        left_repr = right_repr = op = ""
        field_diffs = ()
    return TestResult(
        status=StatusKind.FAILED,
        message=msg,
        file=file,
        lineno=lineno,
        source_line=source_line,
        left=left_repr,
        right=right_repr,
        op=op,
        field_diffs=field_diffs,
        exc_type="AssertionError",
        frames=_get_frames(exc, show_internals=show_internals, show_locals=show_locals),
    )


def _handle_runtime_exception(
    exc: BaseException,
    *,
    show_internals: bool = False,
    show_locals: bool = False,
) -> TestResult | None:
    """Map a non-assertion BaseException to a TestResult, or None to re-raise."""
    exc_type = type(exc).__name__
    if exc_type in ("Skipped", "SkipTest"):
        return TestResult.skipped(str(exc))
    if isinstance(exc, Exception):
        file, lineno, source_line = _get_location(exc)
        return TestResult(
            status=StatusKind.ERROR,
            message=f"{type(exc).__name__}: {exc}",
            file=file,
            lineno=lineno,
            source_line=source_line,
            exc_type=type(exc).__name__,
            frames=_get_frames(
                exc, show_internals=show_internals, show_locals=show_locals
            ),
        )
    return None


def check_warnings(
    caught: list[warnings.WarningMessage],
    all_kwargs: dict[str, Any],
) -> tuple[bool, str]:
    """Filter caught warnings, excluding captured and teardown warnings."""
    warn_capture = next(
        (v for v in all_kwargs.values() if isinstance(v, _WarnCapture)), None
    )
    captured_ids = warn_capture._all_captured_ids if warn_capture else set()
    relevant = "\n".join(
        f"{wi.category.__name__}: {wi.message}"
        for wi in caught
        if not issubclass(wi.category, FixtureTeardownWarning)
        and id(wi) not in captured_ids
    )
    if not relevant:
        return False, ""
    return True, relevant


def is_debuggable(exc: BaseException) -> bool:
    """Return True if the exception should trigger the post-mortem debugger.

    Skips, xfails, keyboard interrupts, and system exits are not debuggable.
    """
    exc_type = type(exc).__name__
    if exc_type in ("Skipped", "SkipTest"):
        return False
    return isinstance(exc, (AssertionError, Exception))


def dispatch_exception(
    exc: BaseException,
    *,
    show_internals: bool = False,
    show_locals: bool = False,
) -> TestResult | None:
    """Map exception to TestResult. Returns None to signal re-raise."""
    if isinstance(exc, AssertionError):
        return _handle_assertion_error(
            exc, show_internals=show_internals, show_locals=show_locals
        )
    return _handle_runtime_exception(
        exc, show_internals=show_internals, show_locals=show_locals
    )
