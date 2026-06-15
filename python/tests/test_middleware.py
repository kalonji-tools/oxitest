from __future__ import annotations

import dataclasses
from types import MappingProxyType

from oxitest._bridge._middleware import (
    ExecutionPlan,
    build_pipeline,
)
from oxitest._bridge.result import StatusKind, WarnedResult


class _UppercaseMiddleware:
    """Test middleware that uppercases the result message."""

    def apply(self, plan: ExecutionPlan, next_fn):
        def wrapped():
            result = next_fn()
            return dataclasses.replace(result, message=result.message.upper())

        return wrapped


class _SkipMiddleware:
    """Test middleware that passes through unchanged."""

    def apply(self, plan: ExecutionPlan, next_fn):
        return next_fn


def test_build_pipeline_no_middlewares():
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return WarnedResult(message="ok")

    execute = build_pipeline([], plan, base)
    result = execute()
    assert result.status == StatusKind.WARNED, f"expected WARNED, got {result.status}"
    assert result.message == "ok", f"expected 'ok', got {result.message!r}"  # ty: ignore[unresolved-attribute]


def test_build_pipeline_single_middleware():
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return WarnedResult(message="hello")

    execute = build_pipeline([_UppercaseMiddleware()], plan, base)
    result = execute()
    assert result.message == "HELLO", f"expected 'HELLO', got {result.message!r}"  # ty: ignore[unresolved-attribute]


def test_build_pipeline_ordering():
    """Last middleware in the list should be outermost (runs first)."""
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return WarnedResult(message="base")

    mws = [_SkipMiddleware(), _UppercaseMiddleware()]
    execute = build_pipeline(mws, plan, base)
    result = execute()
    assert result.message == "BASE", (  # ty: ignore[unresolved-attribute]
        f"uppercase middleware should wrap the base, got {result.message!r}"  # ty: ignore[unresolved-attribute]
    )


def test_build_pipeline_skip_middleware_is_noop():
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=False,
        default_timeout=None,
        backend=None,
        shared_session=None,
    )

    def base():
        return WarnedResult(message="unchanged")

    execute = build_pipeline([_SkipMiddleware()], plan, base)
    result = execute()
    assert result.message == "unchanged", (  # ty: ignore[unresolved-attribute]
        f"skip middleware should not change result, got {result.message!r}"  # ty: ignore[unresolved-attribute]
    )
