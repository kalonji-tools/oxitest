"""Tests for build_pipeline composition and middleware ordering."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncGenerator, Callable
from types import MappingProxyType
from typing import Any

from oxitest._bridge._boundary import advance_async_gen
from oxitest._bridge._middleware import (
    ExecutionPlan,
    _teardown_async_generators,
    build_pipeline,
)
from oxitest._bridge.result import TestResult, WarnedResult
from tests import helpers


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
    )

    def base() -> WarnedResult:
        return WarnedResult(message="ok")

    execute = build_pipeline([], plan, base)
    result = execute()
    helpers.assert_result(
        result,
        WarnedResult,
        why="with nothing installed the pipeline must not produce a result of its own"
        " -- every suite without middleware would otherwise read a fabricated outcome",
        message="ok",
    )


def test_build_pipeline_single_middleware() -> None:
    """A single middleware should wrap the base and transform the result."""
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=False,
    )

    def base() -> WarnedResult:
        return WarnedResult(message="hello")

    execute = build_pipeline([_UppercaseMiddleware()], plan, base)
    result = execute()
    helpers.assert_result(
        result,
        WarnedResult,
        why="the middleware rebuilds via dataclasses.replace, so this pins both halves:"
        " the transform reaches the caller, and the rebuild preserves the variant",
        message="HELLO",
    )


def test_build_pipeline_ordering() -> None:
    """Last middleware in the list should be outermost (runs first)."""
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=False,
    )

    def base() -> WarnedResult:
        return WarnedResult(message="base")

    mws = [_SkipMiddleware(), _UppercaseMiddleware()]
    execute = build_pipeline(mws, plan, base)
    result = execute()
    helpers.assert_result(
        result,
        WarnedResult,
        why="the casing is the entire ordering claim, not a string check -- the"
        " uppercase middleware only sees the base result if it was wrapped outermost",
        message="BASE",
    )


def test_build_pipeline_skip_middleware_is_noop() -> None:
    """A pass-through middleware should leave the result unchanged."""
    plan = ExecutionPlan(
        fn=lambda: None,
        fn_name="test_x",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=False,
    )

    def base() -> WarnedResult:
        return WarnedResult(message="unchanged")

    execute = build_pipeline([_SkipMiddleware()], plan, base)
    result = execute()
    helpers.assert_result(
        result,
        WarnedResult,
        why="returning next_fn itself rather than a closure must still compose -- a"
        " non-empty list is the case the empty-list identity test cannot reach",
        message="unchanged",
    )


def test_async_drain_skips_a_generator_whose_setup_never_completed() -> None:
    """The async teardown list is filled before each generator is advanced.

    Registration precedes the advance so an interrupt cannot strand a set-up
    fixture (#1962), which means this list can legitimately hold a generator
    that never reached its ``yield``. Advancing that one here would *run its
    setup* during teardown — strictly worse than the missed teardown the
    ordering exists to prevent.
    """
    ran: list[str] = []

    async def agen() -> AsyncGenerator[str, None]:
        ran.append("setup")
        yield "v"
        ran.append("teardown")

    unstarted = agen()

    asyncio.run(_teardown_async_generators([("never_started", unstarted)]))

    assert ran == [], (
        f"draining a never-started async fixture must run nothing, but ran "
        f"{ran}. Advancing it here executes the fixture body during teardown"
    )


def test_async_drain_runs_a_generator_that_reached_its_yield() -> None:
    """The guard must not be so broad that it skips real teardowns."""
    ran: list[str] = []

    async def agen() -> AsyncGenerator[str, None]:
        ran.append("setup")
        yield "v"
        ran.append("teardown")

    async def drive() -> None:
        started = agen()
        await advance_async_gen(started)
        await _teardown_async_generators([("started", started)])

    asyncio.run(drive())

    assert ran == ["setup", "teardown"], (
        f"a fixture suspended at its yield must still be torn down; ran {ran}"
    )
