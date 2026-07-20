"""Tests for the two session middleware classes: _SessionRunMiddleware and AsyncBridge.

`_SessionRunMiddleware` covers both the ``Shared`` and ``Arrange`` strategies
(they only differ in session lifetime, expressed at the strategy level and
enforced by ``resolve_strategy``). ``AsyncBridgeMiddleware`` covers ``Fresh``.
"""

from __future__ import annotations

from collections.abc import Callable
from types import MappingProxyType
from typing import Any

from oxitest._bridge._async_backend import AsyncioBackend
from oxitest._bridge._middleware import (
    AsyncBridgeMiddleware,
    ExecutionPlan,
    _SessionRunMiddleware,
)
from oxitest._bridge.result import TestResult, WarnedResult


class _RunTrackingSession:
    """Minimal stand-in for AsyncSession that records run() calls."""

    def __init__(self) -> None:
        self.called = False

    def run(self, coro: Any) -> TestResult:
        self.called = True
        coro.close()  # avoid RuntimeWarning: coroutine was never awaited
        return WarnedResult(message="from-session")


def _make_plan(*, is_async: bool, fn: Callable[..., Any]) -> ExecutionPlan:
    return ExecutionPlan(
        fn=fn,
        fn_name="t",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=is_async,
        default_timeout=None,
        backend=None,
        shared_session=None,
        arrange_session=None,
    )


async def _dummy_async() -> None:
    pass


def _dummy_sync() -> None:
    pass


def _base() -> TestResult:
    return WarnedResult(message="base")


def test_session_run_middleware_wraps_via_session_run() -> None:
    """_SessionRunMiddleware dispatches async tests via session.run().

    This path is used for both Shared and Arrange strategies — the middleware
    itself is agnostic to which strategy variant produced it.
    """
    session = _RunTrackingSession()
    mw = _SessionRunMiddleware(session=session)
    plan = _make_plan(is_async=True, fn=_dummy_async)
    wrapped = mw.apply(plan=plan, next_fn=_base)
    result = wrapped()
    assert session.called, "_SessionRunMiddleware must dispatch via session.run()"
    assert isinstance(result, WarnedResult), (
        "result must be the WarnedResult returned by the tracking session"
    )
    assert result.message == "from-session", (
        "result message must come from the session's run() return value"
    )


def test_session_run_middleware_skips_sync_test() -> None:
    """_SessionRunMiddleware passes next_fn through unchanged for sync tests."""
    session = _RunTrackingSession()
    mw = _SessionRunMiddleware(session=session)
    plan = _make_plan(is_async=False, fn=_dummy_sync)
    wrapped = mw.apply(plan=plan, next_fn=_base)
    assert wrapped is _base, "session middleware must pass through for sync tests"


def test_async_bridge_middleware_acquires_backend_session() -> None:
    """AsyncBridgeMiddleware acquires a fresh backend session and runs the test."""
    backend = AsyncioBackend()
    mw = AsyncBridgeMiddleware(backend=backend)
    plan = _make_plan(is_async=True, fn=_dummy_async)
    wrapped = mw.apply(plan=plan, next_fn=_base)
    result = wrapped()
    assert result.status == "passed", (
        "AsyncBridgeMiddleware fresh-session path must succeed"
    )
