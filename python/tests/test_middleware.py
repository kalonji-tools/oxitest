"""Tests for build_pipeline composition and middleware ordering."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from oxitest import helpers
from oxitest._bridge._middleware import (
    ExecutionPlan,
    build_pipeline,
)
from oxitest._bridge.result import TestResult, WarnedResult


class _UppercaseMiddleware:
    """Test middleware that uppercases the result message."""

    def apply(
        self, *, next_fn: Callable[[], TestResult], **_: Any
    ) -> Callable[[], TestResult]:
        def wrapped() -> TestResult:
            result = next_fn()
            assert isinstance(result, WarnedResult), (
                f"expected WarnedResult, got {type(result).__name__}"
            )
            return dataclasses.replace(result, message=result.message.upper())

        return wrapped


class _SkipMiddleware:
    """Test middleware that passes through unchanged."""

    def apply(
        self, *, next_fn: Callable[[], TestResult], **_: Any
    ) -> Callable[[], TestResult]:
        return next_fn


def test_build_pipeline_no_middlewares() -> None:
    """build_pipeline with no middlewares should call the base function directly."""
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
        arrange_session=None,
    )

    def base() -> WarnedResult:
        return WarnedResult(message="ok")

    execute = build_pipeline([], plan, base)
    result = execute()
    helpers.common.assert_result(result, WarnedResult, message="ok")


def test_build_pipeline_single_middleware() -> None:
    """A single middleware should wrap the base and transform the result."""
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
        arrange_session=None,
    )

    def base() -> WarnedResult:
        return WarnedResult(message="hello")

    execute = build_pipeline([_UppercaseMiddleware()], plan, base)
    result = execute()
    helpers.common.assert_result(result, WarnedResult, message="HELLO")


def test_build_pipeline_ordering() -> None:
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
        arrange_session=None,
    )

    def base() -> WarnedResult:
        return WarnedResult(message="base")

    mws = [_SkipMiddleware(), _UppercaseMiddleware()]
    execute = build_pipeline(mws, plan, base)
    result = execute()
    helpers.common.assert_result(result, WarnedResult, message="BASE")


def test_build_pipeline_skip_middleware_is_noop() -> None:
    """A pass-through middleware should leave the result unchanged."""
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
        arrange_session=None,
    )

    def base() -> WarnedResult:
        return WarnedResult(message="unchanged")

    execute = build_pipeline([_SkipMiddleware()], plan, base)
    result = execute()
    helpers.common.assert_result(result, WarnedResult, message="unchanged")
