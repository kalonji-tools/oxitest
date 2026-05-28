from __future__ import annotations

from oxitest._bridge._middleware import (
    ExecutionPlan,
    build_pipeline,
)
from oxitest._bridge.result import StatusKind, TestResult


class _UppercaseMiddleware:
    """Test middleware that uppercases the result message."""

    def apply(self, plan: ExecutionPlan, next_fn):
        def wrapped():
            result = next_fn()
            return TestResult(status=result.status, message=result.message.upper())

        return wrapped


class _SkipMiddleware:
    """Test middleware that passes through unchanged."""

    def apply(self, plan: ExecutionPlan, next_fn):
        return next_fn


def test_build_pipeline_no_middlewares():
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs={},
        marks=[],
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return TestResult(status=StatusKind.PASSED, message="ok")

    execute = build_pipeline([], plan, base)
    result = execute()
    assert result.status == StatusKind.PASSED, f"expected PASSED, got {result.status}"
    assert result.message == "ok", f"expected 'ok', got {result.message!r}"


def test_build_pipeline_single_middleware():
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs={},
        marks=[],
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return TestResult(status=StatusKind.PASSED, message="hello")

    execute = build_pipeline([_UppercaseMiddleware()], plan, base)
    result = execute()
    assert result.message == "HELLO", f"expected 'HELLO', got {result.message!r}"


def test_build_pipeline_ordering():
    """Last middleware in the list should be outermost (runs first)."""
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs={},
        marks=[],
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return TestResult(status=StatusKind.PASSED, message="base")

    mws = [_SkipMiddleware(), _UppercaseMiddleware()]
    execute = build_pipeline(mws, plan, base)
    result = execute()
    assert result.message == "BASE", (
        f"uppercase middleware should wrap the base, got {result.message!r}"
    )


def test_build_pipeline_skip_middleware_is_noop():
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs={},
        marks=[],
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return TestResult(status=StatusKind.PASSED, message="unchanged")

    execute = build_pipeline([_SkipMiddleware()], plan, base)
    result = execute()
    assert result.message == "unchanged", (
        f"skip middleware should not change result, got {result.message!r}"
    )
