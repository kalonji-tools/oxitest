"""Tests for _check_warnings() and _dispatch_exception() helper functions."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import oxitest as oxi
from oxitest import WarnCapture
from oxitest._bridge._diagnostics import (
    check_warnings as _check_warnings,
    dispatch_exception as _dispatch_exception,
)
from oxitest._bridge.result import ErrorResult, FailedResult, StatusKind
from tests import helpers

# ---------------------------------------------------------------------------
# _check_warnings
# ---------------------------------------------------------------------------


def test_check_warnings_no_warnings() -> None:
    """Empty warning list returns (False, '')."""
    has, msg = _check_warnings([], {})

    assert has is False, "expected no warnings"
    assert msg == "", "expected empty message"


def test_check_warnings_with_relevant_warning() -> None:
    """A normal UserWarning is reported."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("something fishy", UserWarning, stacklevel=1)

    has, msg = _check_warnings(caught, {})

    assert has is True, "expected warnings to be detected"
    assert "UserWarning" in msg, "expected UserWarning category in message"
    assert "something fishy" in msg, "expected warning text in message"


def test_check_warnings_excludes_captured_ids() -> None:
    """Warnings already captured by WarnCapture are filtered out."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("captured one", UserWarning, stacklevel=1)

    wc = WarnCapture()
    # Simulate that the WarnCapture already captured this warning
    wc._all_captured_ids.add(id(caught[0]))  # noqa: SLF001 — test setup: simulate a captured warning

    has, msg = _check_warnings(caught, {"warns": wc})

    assert has is False, "captured warnings should be excluded"
    assert msg == "", "expected empty message for captured warnings"


def test_check_warnings_mixed() -> None:
    """Only non-captured warnings are returned."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("relevant one", UserWarning, stacklevel=1)
        warnings.warn("relevant two", DeprecationWarning, stacklevel=1)

    has, msg = _check_warnings(caught, {})

    assert has is True, "expected relevant warnings detected"
    assert "relevant one" in msg, "expected first relevant warning"
    assert "relevant two" in msg, "expected second relevant warning"


# ---------------------------------------------------------------------------
# _dispatch_exception
# ---------------------------------------------------------------------------


def test_dispatch_assertion_error() -> None:
    """AssertionError maps to a FAILED TestResult."""
    exc = AssertionError("expected 1 got 2")

    result = _dispatch_exception(exc)

    helpers.assert_result(
        result,
        FailedResult,
        why="this pins _dispatch_exception's *routing*, not the classification"
        " (test_executor_internals covers that) -- the variant is the only observable"
        " proof that an AssertionError reached the assertion arm",
        exc_type="AssertionError",
    )


def test_dispatch_runtime_exception() -> None:
    """A normal Exception maps to an ERROR TestResult."""
    exc = ValueError("bad value")

    result = _dispatch_exception(exc)

    error = helpers.assert_result(
        result,
        ErrorResult,
        why="the routing claim again, for the fallback arm -- a non-AssertionError"
        " must reach the runtime handler, and the variant is the only proof it did",
    )
    assert "ValueError" in error.message, "expected ValueError in message"


def test_dispatch_skip_exception() -> None:
    """Skip-type exceptions map to SKIPPED."""

    class Skipped(Exception):  # noqa: N818
        pass

    exc = Skipped("not today")

    result = _dispatch_exception(exc)

    assert result is not None, "Skipped should return a result"
    assert result.status == StatusKind.SKIPPED, "expected SKIPPED status"


def test_dispatch_keyboard_interrupt() -> None:
    """Non-Exception BaseException returns None (signals re-raise)."""
    exc = KeyboardInterrupt()

    result = _dispatch_exception(exc)

    assert result is None, "KeyboardInterrupt should return None"


@dataclass(frozen=True)
class _BaseExcCase:
    exc_class: type


@oxi.parametrize(
    keyboard_interrupt=_BaseExcCase(exc_class=KeyboardInterrupt),
    system_exit=_BaseExcCase(exc_class=SystemExit),
    generator_exit=_BaseExcCase(exc_class=GeneratorExit),
)
def test_dispatch_base_exceptions_return_none(exc_class: type) -> None:
    """All non-Exception BaseExceptions return None."""
    result = _dispatch_exception(exc_class())

    assert result is None, f"{exc_class.__name__} should return None"
