"""Tests for _check_warnings() and _dispatch_exception() helper functions."""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import oxitest as oxi
from oxitest._bridge._builtins._warncapture import _WarnCapture
from oxitest._bridge._middleware import _check_warnings, _dispatch_exception
from oxitest._bridge.fixtures import FixtureTeardownWarning
from oxitest._bridge.result import StatusKind

# ---------------------------------------------------------------------------
# _check_warnings
# ---------------------------------------------------------------------------


def test_check_warnings_no_warnings():
    """Empty warning list returns (False, '')."""
    has, msg = _check_warnings([], {})

    assert has is False, "expected no warnings"
    assert msg == "", "expected empty message"


def test_check_warnings_with_relevant_warning():
    """A normal UserWarning is reported."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("something fishy", UserWarning, stacklevel=1)

    has, msg = _check_warnings(caught, {})

    assert has is True, "expected warnings to be detected"
    assert "UserWarning" in msg, "expected UserWarning category in message"
    assert "something fishy" in msg, "expected warning text in message"


def test_check_warnings_excludes_teardown_warnings():
    """FixtureTeardownWarning is filtered out."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn(FixtureTeardownWarning("teardown oops"), stacklevel=1)

    has, msg = _check_warnings(caught, {})

    assert has is False, "teardown warnings should be excluded"
    assert msg == "", "expected empty message for teardown-only warnings"


def test_check_warnings_excludes_captured_ids():
    """Warnings already captured by _WarnCapture are filtered out."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("captured one", UserWarning, stacklevel=1)

    wc = _WarnCapture()
    # Simulate that the WarnCapture already captured this warning
    wc._all_captured_ids.add(id(caught[0]))

    has, msg = _check_warnings(caught, {"warns": wc})

    assert has is False, "captured warnings should be excluded"
    assert msg == "", "expected empty message for captured warnings"


def test_check_warnings_mixed():
    """Only non-teardown, non-captured warnings are returned."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        warnings.warn("relevant one", UserWarning, stacklevel=1)
        warnings.warn(FixtureTeardownWarning("teardown"), stacklevel=1)
        warnings.warn("relevant two", DeprecationWarning, stacklevel=1)

    has, msg = _check_warnings(caught, {})

    assert has is True, "expected relevant warnings detected"
    assert "relevant one" in msg, "expected first relevant warning"
    assert "relevant two" in msg, "expected second relevant warning"
    assert "teardown" not in msg, "teardown warning should be excluded"


# ---------------------------------------------------------------------------
# _dispatch_exception
# ---------------------------------------------------------------------------


def test_dispatch_assertion_error():
    """AssertionError maps to a FAILED TestResult."""
    exc = AssertionError("expected 1 got 2")

    result = _dispatch_exception(exc)

    assert result is not None, "AssertionError should return a result"
    assert result.status == StatusKind.FAILED, "expected FAILED status"
    assert result.exc_type == "AssertionError", "expected AssertionError exc_type"


def test_dispatch_runtime_exception():
    """A normal Exception maps to an ERROR TestResult."""
    exc = ValueError("bad value")

    result = _dispatch_exception(exc)

    assert result is not None, "ValueError should return a result"
    assert result.status == StatusKind.ERROR, "expected ERROR status"
    assert "ValueError" in result.message, "expected ValueError in message"


def test_dispatch_skip_exception():
    """Skip-type exceptions map to SKIPPED."""

    class Skipped(Exception):  # noqa: N818
        pass

    exc = Skipped("not today")

    result = _dispatch_exception(exc)

    assert result is not None, "Skipped should return a result"
    assert result.status == StatusKind.SKIPPED, "expected SKIPPED status"


def test_dispatch_keyboard_interrupt():
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
def test_dispatch_base_exceptions_return_none(exc_class: type):
    """All non-Exception BaseExceptions return None."""
    result = _dispatch_exception(exc_class())

    assert result is None, f"{exc_class.__name__} should return None"
