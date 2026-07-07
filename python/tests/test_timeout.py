from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from types import MappingProxyType

import oxitest
import oxitest as oxi
from oxitest import helpers, raises
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._mark_registry import _TimeoutHandler
from oxitest._bridge._timeout import OxitestTimeoutError, _timeout_context
from oxitest._bridge.result import PassedResult, TimeoutResult


def test_timeout_context_raises_on_expiry() -> None:
    with raises(OxitestTimeoutError), _timeout_context(1):
        time.sleep(5)


def test_timeout_context_does_not_raise_when_fast() -> None:
    with _timeout_context(5):
        time.sleep(0)  # completes instantly


def test_timeout_context_cancels_after_block() -> None:
    """No residual alarm after a successful block (Unix only)."""
    import signal as _signal

    if not hasattr(_signal, "getitimer"):
        return  # Windows: no way to inspect pending alarm, skip
    with _timeout_context(5):
        pass
    remaining = _signal.getitimer(_signal.ITIMER_REAL)[0]
    assert remaining == 0.0, f"Expected no pending alarm, got {remaining}s remaining"


def test_oxitest_timeout_error_is_exception() -> None:
    assert issubclass(OxitestTimeoutError, Exception), (
        "OxitestTimeoutError should be a subclass of Exception"
    )


def test_timeout_context_type_matches_platform() -> None:
    if sys.platform == "win32":
        from oxitest._bridge._timeout import _WindowsTimeoutContext

        assert isinstance(_timeout_context(1), _WindowsTimeoutContext), (
            "on Windows, _timeout_context() should return a _WindowsTimeoutContext"
        )
    else:
        from oxitest._bridge._timeout import _UnixTimeoutContext

        assert isinstance(_timeout_context(1), _UnixTimeoutContext), (
            "on Unix, _timeout_context() should return a _UnixTimeoutContext"
        )


@dataclass(frozen=True)
class InvalidTimeout:
    seconds: int


@oxi.parametrize(
    zero=InvalidTimeout(seconds=0),
    negative=InvalidTimeout(seconds=-1),
)
def test_timeout_mark_rejects_invalid_seconds(seconds) -> None:
    with raises(ValueError, match="seconds > 0"):

        @oxitest.mark.timeout(seconds=seconds)
        def test_bad():
            pass


def test_timeout_mark_stores_seconds() -> None:
    @oxitest.mark.timeout(seconds=5)
    def test_ok():
        pass

    from oxitest._bridge._fn_metadata import get_metadata

    marks = get_metadata(test_ok).marks
    assert len(marks) == 1, (
        f"expected 1 mark on test_ok after @mark.timeout, got {len(marks)}: {marks}"
    )
    assert marks[0].name == "timeout", (
        f"expected mark name 'timeout', got {marks[0].name!r}"
    )
    assert marks[0].kwargs["seconds"] == 5, (
        f"expected mark kwargs['seconds'] == 5, got {marks[0].kwargs.get('seconds')!r}"
    )


def test_timeout_handler_returns_wrapper() -> None:
    result = _TimeoutHandler().handle(
        MarkInfo("timeout", (), MappingProxyType({"seconds": 3}))
    )
    assert result.wrapper is not None, (
        "TimeoutHandler.handle() should return a wrapper, got None"
    )
    assert result.short_circuit is None, (
        "TimeoutHandler.handle() should not short-circuit (short_circuit should be "
        "None)"
    )


def test_timeout_handler_wrapper_passes_fast_test() -> None:
    result = _TimeoutHandler().handle(
        MarkInfo("timeout", (), MappingProxyType({"seconds": 5}))
    )
    wrapper = result.wrapper
    assert wrapper is not None, "TimeoutHandler.handle() should produce a wrapper"
    fast_result = PassedResult()
    assert wrapper(lambda: fast_result).status == "passed", (
        "timeout wrapper should pass through 'passed' result when test finishes quickly"
    )


def test_timeout_handler_wrapper_returns_timeout_on_expiry() -> None:
    result = _TimeoutHandler().handle(
        MarkInfo("timeout", (), MappingProxyType({"seconds": 1}))
    )
    wrapper = result.wrapper
    assert wrapper is not None, "TimeoutHandler.handle() should produce a wrapper"

    def slow_next():
        time.sleep(5)
        return PassedResult()

    outcome = wrapper(slow_next)
    r = helpers.common.assert_result(outcome, TimeoutResult)
    assert "1s" in r.message, (
        f"timeout message should mention the limit '1s', got {r.message!r}"
    )
