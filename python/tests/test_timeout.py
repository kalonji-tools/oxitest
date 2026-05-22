from __future__ import annotations

import sys
import time

import oxitest
from oxitest import raises
from oxitest._bridge._fixture_session import _NullFixtureSession
from oxitest._bridge._mark_api import MarkInfo
from oxitest._bridge._mark_registry import _HandlerContext, _TimeoutHandler
from oxitest._bridge._timeout import OxitestTimeoutError, _timeout_context
from oxitest._bridge.result import StatusKind, TestResult


def test_timeout_context_raises_on_expiry():
    with raises(OxitestTimeoutError), _timeout_context(1):
        time.sleep(5)


def test_timeout_context_does_not_raise_when_fast():
    with _timeout_context(5):
        time.sleep(0)  # completes instantly


def test_timeout_context_cancels_after_block():
    """No residual alarm after a successful block (Unix only)."""
    import signal as _signal

    if not hasattr(_signal, "getitimer"):
        return  # Windows: no way to inspect pending alarm, skip
    with _timeout_context(5):
        pass
    remaining = _signal.getitimer(_signal.ITIMER_REAL)[0]
    assert remaining == 0.0, f"Expected no pending alarm, got {remaining}s remaining"


def test_oxitest_timeout_error_is_exception():
    assert issubclass(OxitestTimeoutError, Exception), (
        "OxitestTimeoutError should be a subclass of Exception"
    )


def test_timeout_context_type_matches_platform():
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


def _timeout_ctx(default_timeout=None):
    def fn():
        pass

    return _HandlerContext(
        fn_raw=fn,
        fn=fn,
        all_kwargs={},
        session=_NullFixtureSession(),
        module_path="fake.py",
        fn_teardowns=[],
        default_timeout=default_timeout,
    )


def test_timeout_mark_validates_seconds_gt_zero():
    with raises(ValueError, match="seconds > 0"):

        @oxitest.mark.timeout(seconds=0)
        def test_bad():
            pass


def test_timeout_mark_validates_negative():
    with raises(ValueError, match="seconds > 0"):

        @oxitest.mark.timeout(seconds=-1)
        def test_bad():
            pass


def test_timeout_mark_stores_seconds():
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


def test_timeout_handler_returns_wrapper():
    ctx = _timeout_ctx()
    result = _TimeoutHandler().handle(MarkInfo("timeout", (), {"seconds": 3}), ctx)
    assert result.wrapper is not None, (
        "TimeoutHandler.handle() should return a wrapper, got None"
    )
    assert result.short_circuit is None, (
        "TimeoutHandler.handle() should not short-circuit (short_circuit should be "
        "None)"
    )


def test_timeout_handler_wrapper_passes_fast_test():
    ctx = _timeout_ctx()
    result = _TimeoutHandler().handle(MarkInfo("timeout", (), {"seconds": 5}), ctx)
    wrapper = result.wrapper
    assert wrapper is not None, "TimeoutHandler.handle() should produce a wrapper"
    fast_result = TestResult(status=StatusKind.PASSED)
    assert wrapper(lambda: fast_result).status == "passed", (
        "timeout wrapper should pass through 'passed' result when test finishes quickly"
    )


def test_timeout_handler_wrapper_returns_timeout_on_expiry():
    ctx = _timeout_ctx()
    result = _TimeoutHandler().handle(MarkInfo("timeout", (), {"seconds": 1}), ctx)
    wrapper = result.wrapper
    assert wrapper is not None, "TimeoutHandler.handle() should produce a wrapper"

    def slow_next():
        time.sleep(5)
        return TestResult(status=StatusKind.PASSED)

    outcome = wrapper(slow_next)
    assert outcome.status == "timeout", (
        f"expected status='timeout' when test exceeds limit, got {outcome.status!r}"
    )
    assert "1s" in outcome.message, (
        f"timeout message should mention the limit '1s', got {outcome.message!r}"
    )
