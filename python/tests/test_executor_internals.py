"""Unit tests for executor helper functions."""

from __future__ import annotations

from oxitest import TempDir
from oxitest._bridge.ast_rewriter import _OXITEST_NO_RHS, _OxitestAssertionError
from oxitest._bridge.executor import (
    _compose,
    _handle_assertion_error,
    _handle_runtime_exception,
)
from oxitest._bridge.result import TestResult


def test_plain_assertion_returns_failed():
    exc = AssertionError("plain message")
    result = _handle_assertion_error(exc)
    assert result.status == "failed", (
        f"plain AssertionError should produce status='failed', got {result.status!r}"
    )
    assert result.message == "plain message", (
        f"result message should be 'plain message', got {result.message!r}"
    )


def test_plain_assertion_no_message_gives_empty_message():
    exc = AssertionError()
    result = _handle_assertion_error(exc)
    assert result.status == "failed", (
        f"AssertionError() should produce status='failed', got {result.status!r}"
    )
    assert result.message == "", (
        f"AssertionError() with no args should give empty message, got "
        f"{result.message!r}"
    )


def test_oxitest_assertion_with_lhs_rhs_populates_fields():
    exc = _OxitestAssertionError(1, 2, "==", "mismatch")
    result = _handle_assertion_error(exc)
    assert result.status == "failed", (
        f"_OxitestAssertionError should produce status='failed', got {result.status!r}"
    )
    assert result.left == "1", (
        f"result.left should be '1' (repr of lhs), got {result.left!r}"
    )
    assert result.right == "2", (
        f"result.right should be '2' (repr of rhs), got {result.right!r}"
    )
    assert result.op == "==", f"result.op should be '==', got {result.op!r}"


def test_oxitest_assertion_no_rhs_gives_empty_right():
    exc = _OxitestAssertionError(42, _OXITEST_NO_RHS, "==", "")
    result = _handle_assertion_error(exc)
    assert result.right == "", (
        f"_OXITEST_NO_RHS sentinel should produce empty result.right, got "
        f"{result.right!r}"
    )


def test_skipped_exception_returns_skipped():
    class Skipped(Exception):
        pass

    exc = Skipped("skip reason")
    result = _handle_runtime_exception(exc)
    assert result is not None, "Skipped exception should return a TestResult, not None"
    assert result.status == "skipped", (
        f"Skipped exception should produce status='skipped', got {result.status!r}"
    )
    assert result.message == "skip reason", (
        f"skipped result message should be 'skip reason', got {result.message!r}"
    )


def test_skip_test_returns_skipped():
    class SkipTest(Exception):
        pass

    exc = SkipTest("reason")
    result = _handle_runtime_exception(exc)
    assert result is not None, "SkipTest exception should return a TestResult, not None"
    assert result.status == "skipped", (
        f"SkipTest exception should produce status='skipped', got {result.status!r}"
    )


def test_regular_exception_returns_error():
    try:
        raise ValueError("something broke")
    except ValueError as exc:
        result = _handle_runtime_exception(exc)
    assert result is not None, "ValueError should return a TestResult, not None"
    assert result.status == "error", (
        f"ValueError should produce status='error', got {result.status!r}"
    )
    assert "ValueError" in result.message, (
        f"error message should contain 'ValueError', got {result.message!r}"
    )


def test_base_exception_not_exception_returns_none():
    class MyBase(BaseException):
        pass

    result = _handle_runtime_exception(MyBase("raw"))
    assert result is None, (  # caller must re-raise
        f"BaseException (non-Exception) should return None so caller can re-raise, "
        f"got {result!r}"
    )


def test_compose_wraps_inner():
    """wrapper sees inner's result and can transform it."""

    def inner():
        return TestResult(status="passed")

    def transform(next_fn):
        next_fn()
        return TestResult(status="warned", message="wrapped")

    composed = _compose(transform, inner)
    result = composed()
    assert result.status == "warned", (
        f"composed wrapper should produce status='warned', got {result.status!r}"
    )


def test_compose_passes_through():
    def inner():
        return TestResult(status="failed")

    def wrapper(next_fn):
        return next_fn()

    composed = _compose(wrapper, inner)
    assert composed().status == "failed", (
        f"pass-through wrapper should propagate inner status='failed', got "
        f"{composed().status!r}"
    )


def test_compose_chains_left_to_right():
    """Last appended wrapper = outermost. _compose is called in reversed."""
    calls = []

    def w1(next_fn):
        calls.append("w1")
        return next_fn()

    def w2(next_fn):
        calls.append("w2")
        return next_fn()

    def base():
        return TestResult(status="passed")

    # Simulates: for wrapper in reversed([w1, w2]):
    #   execute = _compose(wrapper, execute)
    # reversed([w1, w2]) = [w2, w1]
    execute = base
    execute = _compose(w2, execute)
    execute = _compose(w1, execute)
    execute()
    assert calls == ["w1", "w2"], (
        f"wrappers should be called in order w1→w2, got {calls}"
    )


def test_repr_max_is_positive_int():
    from oxitest._bridge.executor import _REPR_MAX

    assert isinstance(_REPR_MAX, int), (
        f"_REPR_MAX should be an int, got {type(_REPR_MAX).__name__}"
    )
    assert _REPR_MAX > 0, f"_REPR_MAX should be positive, got {_REPR_MAX}"


def test_repr_safe_truncates_long_string():
    from oxitest._bridge.executor import _REPR_MAX, _repr_safe

    long_str = "x" * (_REPR_MAX * 10)
    result = _repr_safe(long_str)
    assert len(result) <= _REPR_MAX + 20, (
        f"_repr_safe should truncate to at most _REPR_MAX+20={_REPR_MAX + 20} "
        "chars, "
        f"got {len(result)}"
    )


def test_frames_captured_on_assertion_error():
    """_handle_assertion_error populates frames from the traceback."""

    def inner():
        assert False, "boom"  # noqa: B011

    try:
        inner()
    except AssertionError as exc:
        result = _handle_assertion_error(exc)

    assert result.status == "failed", f"expected failed, got {result.status!r}"
    assert len(result.frames) >= 2, (
        f"Expected at least 2 frames (test + inner), got {len(result.frames)}"
    )
    assert result.frames[-1].name == "inner", (
        f"last frame should be 'inner', got {result.frames[-1].name!r}"
    )
    assert result.frames[-1].lineno > 0, (
        f"lineno should be positive, got {result.frames[-1].lineno}"
    )


def test_frames_captured_on_runtime_exception():
    """_handle_runtime_exception populates frames from the traceback."""

    def blow_up():
        raise ValueError("kaboom")

    try:
        blow_up()
    except Exception as exc:
        result = _handle_runtime_exception(exc)

    assert result is not None, "runtime exception should return a result"
    assert result.status == "error", f"expected error, got {result.status!r}"
    assert len(result.frames) >= 2, (
        f"Expected at least 2 frames, got {len(result.frames)}"
    )
    assert result.frames[-1].name == "blow_up", (
        f"last frame should be 'blow_up', got {result.frames[-1].name!r}"
    )


def test_frames_empty_when_no_traceback():
    """An exception without __traceback__ produces empty frames."""
    exc = ValueError("no tb")
    exc.__traceback__ = None
    result = _handle_runtime_exception(exc)
    assert result is not None, "should return a result even without traceback"
    assert result.frames == [], f"expected empty frames, got {result.frames!r}"


def test_bad_module_path_returns_error(tmp: TempDir):
    from oxitest._bridge.executor import run_test

    result = run_test(str(tmp / "nonexistent.py"), "test_foo")
    assert result.status == "error", (
        f"run_test with nonexistent module should return status='error', got "
        f"{result.status!r}"
    )


def test_bad_fn_name_returns_error(tmp: TempDir):
    from oxitest._bridge.executor import run_test

    module = tmp / "test_mod.py"
    module.write_text("def test_real(): pass\n")
    result = run_test(str(module), "test_missing")
    assert result.status == "error", (
        f"run_test with missing function name should return status='error', got "
        f"{result.status!r}"
    )
