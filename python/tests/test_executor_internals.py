"""Unit tests for executor helper functions."""

from __future__ import annotations

from collections.abc import Callable

from oxitest import TempDir, helpers
from oxitest._bridge._assert_error import _OXITEST_NO_RHS, _OxitestAssertionError
from oxitest._bridge._diagnostics import (
    _REPR_MAX,
    _handle_assertion_error,
    _handle_runtime_exception,
    _repr_safe,
)
from oxitest._bridge._middleware import _compose
from oxitest._bridge.result import (
    ErrorResult,
    FailedResult,
    PassedResult,
    SkippedResult,
    TestResult,
    WarnedResult,
)


def test_plain_assertion_returns_failed() -> None:
    """A plain AssertionError produces a FailedResult with status='failed'."""
    exc = AssertionError("plain message")
    result = _handle_assertion_error(exc)
    assert result.status == "failed", (
        "AssertionError maps to 'failed' -- this is the core assertion-to-result"
        " contract"
    )
    assert result.message == "plain message", (
        "the original exception text must survive intact so test output shows the"
        " developer's own words"
    )


def test_plain_assertion_no_message_gives_empty_message() -> None:
    """AssertionError with no args produces an empty message string, not None."""
    exc = AssertionError()
    result = _handle_assertion_error(exc)
    assert result.status == "failed", (
        "even a bare AssertionError (no args) is still a test failure -- the runner"
        " must not silently pass it"
    )
    assert result.message == "", (
        "empty string (not None) ensures downstream code can safely .format() or"
        " concatenate the message"
    )


def test_oxitest_assertion_with_lhs_rhs_populates_fields() -> None:
    """_OxitestAssertionError with both operands fills left, right, and op."""
    exc = _OxitestAssertionError(1, 2, "==", "mismatch")
    result = _handle_assertion_error(exc)
    assert result.status == "failed", (
        "enriched assertions are still failures -- extra diagnostics must not change"
        " the outcome status"
    )
    assert result.left == "1", (
        "left operand feeds the diff view in reporters -- a wrong value produces"
        " misleading diagnostics"
    )
    assert result.right == "2", (
        "right operand feeds the diff view in reporters -- a wrong value produces"
        " misleading diagnostics"
    )
    assert result.op == "==", (
        "the operator is displayed between left and right in failure output -- wrong op"
        " misleads the developer"
    )


def test_oxitest_assertion_no_rhs_gives_empty_right() -> None:
    """The _OXITEST_NO_RHS sentinel maps to an empty string in result.right."""
    exc = _OxitestAssertionError(42, _OXITEST_NO_RHS, "==", "")
    result = _handle_assertion_error(exc)
    assert result.right == "", (
        "unary assertions (e.g. `assert x`) have no RHS -- reporters must get empty"
        " string, not the sentinel object's repr"
    )


def test_skipped_exception_returns_skipped() -> None:
    """Any exception whose class name contains 'Skipped' produces a SkippedResult."""

    class Skipped(Exception):  # noqa: N818
        pass

    exc = Skipped("skip reason")
    result = _handle_runtime_exception(exc)
    helpers.common.assert_result(result, SkippedResult, message="skip reason")


def test_skip_test_returns_skipped() -> None:
    """An exception named 'SkipTest' (pytest convention) produces SkippedResult."""

    class SkipTest(Exception):  # noqa: N818
        pass

    exc = SkipTest("reason")
    result = _handle_runtime_exception(exc)
    assert result is not None, (
        "SkipTest must be caught and converted -- returning None would cause the caller"
        " to re-raise it as an unhandled crash"
    )
    assert result.status == "skipped", (
        "SkipTest is pytest's skip convention -- misclassifying it as error/failed"
        " breaks skip-count accuracy in reports"
    )


def test_regular_exception_returns_error() -> None:
    """An ordinary Exception (not skip/assert) produces ErrorResult with type name."""
    exc = helpers.common.make_exc(ValueError, "something broke")
    result = _handle_runtime_exception(exc)
    r = helpers.common.assert_result(result, ErrorResult)
    assert "ValueError" in r.message, (
        "the exception type name must appear in the message so developers can identify"
        " the error class without a traceback"
    )


def test_base_exception_not_exception_returns_none() -> None:
    """Non-Exception BaseException subclasses return None (caller re-raises)."""

    class MyBase(BaseException):
        pass

    result = _handle_runtime_exception(MyBase("raw"))
    assert result is None, (  # caller must re-raise
        "KeyboardInterrupt/SystemExit must bubble up -- returning a result would"
        " swallow shutdown signals"
    )


def test_compose_wraps_inner() -> None:
    """Wrapper sees inner's result and can transform it."""

    def inner() -> PassedResult:
        return PassedResult()

    def transform(next_fn: Callable[[], TestResult]) -> WarnedResult:
        next_fn()
        return WarnedResult(message="wrapped")

    composed = _compose(transform, inner)
    result = composed()
    assert result.status == "warned", (
        "wrappers must be able to override the inner result -- this is how"
        " timeout/xfail transform outcomes"
    )


def test_compose_passes_through() -> None:
    """A wrapper that delegates to next_fn propagates the inner result unchanged."""

    def inner() -> FailedResult:
        return FailedResult()

    def wrapper(next_fn: Callable[[], TestResult]) -> TestResult:
        return next_fn()

    composed = _compose(wrapper, inner)
    assert composed().status == "failed", (
        "a no-op wrapper must not alter the result -- silent mutation would make"
        " middleware debugging impossible"
    )


def test_compose_chains_left_to_right() -> None:
    """Last appended wrapper = outermost. _compose is called in reversed."""
    calls = []

    def w1(next_fn: Callable[[], TestResult]) -> TestResult:
        calls.append("w1")
        return next_fn()

    def w2(next_fn: Callable[[], TestResult]) -> TestResult:
        calls.append("w2")
        return next_fn()

    def base() -> PassedResult:
        return PassedResult()

    # Manually compose in reverse order (w2 then w1) to mirror the
    # reversed iteration the executor uses at runtime.
    execute = base
    execute = _compose(w2, execute)
    execute = _compose(w1, execute)
    execute()
    assert calls == ["w1", "w2"], (
        "execution order must match reversed composition order -- outermost wrapper"
        " runs first (onion model)"
    )


def test_repr_max_is_positive_int() -> None:
    """_REPR_MAX is a positive integer that caps repr output length."""
    assert isinstance(_REPR_MAX, int), (
        "_REPR_MAX is used in slicing and length comparisons -- a non-int would cause"
        " TypeError at truncation time"
    )
    assert _REPR_MAX > 0, (
        "a zero or negative cap would truncate every repr to nothing, hiding all"
        " diagnostic values from failure output"
    )


def test_repr_safe_truncates_long_string() -> None:
    """_repr_safe caps output length to prevent huge failure messages on long values."""
    long_str = "x" * (_REPR_MAX * 10)
    result = _repr_safe(long_str)
    assert len(result) <= _REPR_MAX + 20, (
        "unbounded repr output can blow up terminal buffers and CI log storage on"
        " pathological values"
    )


def test_frames_captured_on_assertion_error() -> None:
    """_handle_assertion_error populates frames from the traceback."""

    def inner() -> None:
        msg = "boom"
        raise AssertionError(msg)

    try:
        inner()
    except AssertionError as exc:
        result = _handle_assertion_error(exc)

    assert result.status == "failed", (
        "an assertion error with a live traceback must still map to 'failed' -- frame"
        " capture must not alter the outcome"
    )
    assert len(result.frames) >= 2, (
        "at least test + inner frames are needed -- fewer means the traceback walker is"
        " dropping frames developers need to locate the failure"
    )
    assert result.frames[-1].name == "inner", (
        "the deepest frame must be the raise site -- reporters show it first so the"
        " developer sees the exact failing line"
    )
    assert result.frames[-1].lineno > 0, (
        "a zero or negative lineno would make source-view and editor jump links point"
        " nowhere"
    )


def test_frames_captured_on_runtime_exception() -> None:
    """_handle_runtime_exception populates frames from the traceback."""

    def blow_up() -> None:
        msg = "kaboom"
        raise ValueError(msg)

    try:
        blow_up()
    except Exception as exc:  # noqa: BLE001 — test intentionally catches to verify error handling
        result = _handle_runtime_exception(exc)

    r = helpers.common.assert_result(result, ErrorResult)
    assert len(r.frames) >= 2, (
        "runtime errors need the same frame depth as assertion errors -- incomplete"
        " traces leave developers guessing at the crash site"
    )
    assert r.frames[-1].name == "blow_up", (
        "the innermost frame must name the raise site so error reports pinpoint the"
        " exact function that crashed"
    )


def test_frames_empty_when_no_traceback() -> None:
    """An exception without __traceback__ produces empty frames."""
    exc = ValueError("no tb")
    exc.__traceback__ = None
    result = _handle_runtime_exception(exc)
    helpers.common.assert_result(result, ErrorResult, frames=())


def test_bad_module_path_returns_error(tmp: TempDir) -> None:
    """run_test returns an error result when the module path does not exist."""
    result = helpers.common.run_test(str(tmp / "nonexistent.py"), "test_foo")
    assert result.status == "error", (
        "a missing module is an infrastructure error, not a test failure --"
        " misclassifying it hides broken test collection from the developer"
    )


def test_bad_fn_name_returns_error(tmp: TempDir) -> None:
    """run_test returns an error when the function name is absent from the module."""
    module = tmp / "test_mod.py"
    module.write_text("def test_real(): pass\n")
    result = helpers.common.run_test(str(module), "test_missing")
    assert result.status == "error", (
        "a missing function name means the collector and executor disagree on what"
        " exists -- this must surface as an error, not a silent pass"
    )
