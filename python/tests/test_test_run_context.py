from __future__ import annotations

from oxitest._bridge._fixture_context import TestRunContext, _test_run_context


def test_default_is_none() -> None:
    assert _test_run_context.get() is None, "default should be None"


def test_set_and_read() -> None:
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
    ctx = TestRunContext(keep_tmp="always")
    token = _test_run_context.set(ctx)
    _test_run_context.reset(token)
    assert _test_run_context.get() is None, "should be None after reset"


def test_defaults() -> None:
    ctx = TestRunContext()
    assert ctx.keep_tmp is None, f"expected None, got {ctx.keep_tmp!r}"
    assert ctx.result_cell is None, f"expected None, got {ctx.result_cell!r}"
