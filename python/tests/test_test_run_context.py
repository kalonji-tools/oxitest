"""Tests for TestRunContext ContextVar isolation and default field values."""

from __future__ import annotations

from oxitest._bridge._fixture_context import TestRunContext, _test_run_context


def test_default_is_none() -> None:
    """_test_run_context.get() should return None before any context is set."""
    assert _test_run_context.get() is None, "default should be None"


def test_set_and_read() -> None:
    """Set context should be retrievable via get() within the same scope."""
    ctx = TestRunContext(keep_tmp="failed", result_cell=[None])
    token = _test_run_context.set(ctx)
    try:
        got = _test_run_context.get()
        assert got is ctx, f"expected same context, got {got!r}"
        assert got.keep_tmp == "failed", f"expected 'failed', got {got.keep_tmp!r}"
        assert got.result_cell == [None], f"expected [None], got {got.result_cell!r}"
    finally:
        _test_run_context.reset(token)


def test_reset_restores_none() -> None:
    """reset() should restore the ContextVar to its default None value."""
    ctx = TestRunContext(keep_tmp="always")
    token = _test_run_context.set(ctx)
    _test_run_context.reset(token)
    assert _test_run_context.get() is None, "should be None after reset"


def test_defaults() -> None:
    """TestRunContext default fields should be None when no arguments are provided."""
    ctx = TestRunContext()
    assert ctx.keep_tmp is None, f"expected None, got {ctx.keep_tmp!r}"
    assert ctx.result_cell is None, f"expected None, got {ctx.result_cell!r}"
