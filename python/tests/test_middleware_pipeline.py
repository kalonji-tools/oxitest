"""Tests for _MiddlewarePipeline.build_for chain assembly."""

from __future__ import annotations

from collections.abc import Coroutine
from types import MappingProxyType
from typing import Any, TypeVar

from oxitest._bridge._async_backend import AsyncioBackend
from oxitest._bridge._middleware import (
    Arrange,
    AsyncBridgeMiddleware,
    AsyncDepGuardMiddleware,
    ExecutionPlan,
    Fresh,
    SessionStrategy,
    Shared,
    TimeoutMiddleware,
    _MiddlewarePipeline,
    _SessionRunMiddleware,
)
from oxitest._bridge._timeout import TimeoutOff, TimeoutSet

_T = TypeVar("_T")


class _StubSession:
    """Minimal stand-in for AsyncSession — pipeline.build_for only stores references.

    run() is never called by build_for but must exist to satisfy the
    AsyncSession Protocol for ty.
    """

    def run(self, _coro: Coroutine[Any, Any, _T], /) -> _T:
        """Stub — never called by build_for."""
        msg = "_StubSession.run should not be called in pipeline tests"
        raise AssertionError(msg)


def _plan(*, is_async: bool) -> ExecutionPlan:
    def _fn() -> None:
        pass

    return ExecutionPlan(
        fn=_fn,
        fn_name="t",
        kwargs=MappingProxyType({}),
        marks=(),
        no_message_lines=(),
        is_async=is_async,
    )


def test_sync_test_omits_session_middleware() -> None:
    """Sync tests must not have any session middleware appended to the chain."""
    pipeline = _MiddlewarePipeline(timeout=TimeoutOff())
    plan = _plan(is_async=False)
    strategy: SessionStrategy = Fresh(backend=AsyncioBackend())
    result = pipeline.build_for(plan, strategy)
    types = [type(m) for m in result]
    assert types == [AsyncDepGuardMiddleware, TimeoutMiddleware], (
        "sync tests must not append any session middleware"
    )


def test_async_test_fresh_appends_async_bridge() -> None:
    """Fresh strategy appends AsyncBridgeMiddleware for async tests."""
    pipeline = _MiddlewarePipeline(timeout=TimeoutOff())
    plan = _plan(is_async=True)
    strategy: SessionStrategy = Fresh(backend=AsyncioBackend())
    result = pipeline.build_for(plan, strategy)
    types = [type(m) for m in result]
    assert types == [
        AsyncDepGuardMiddleware,
        TimeoutMiddleware,
        AsyncBridgeMiddleware,
    ], "Fresh strategy must add AsyncBridgeMiddleware last"


def test_async_test_shared_appends_session_run_middleware() -> None:
    """Shared strategy appends _SessionRunMiddleware for async tests."""
    pipeline = _MiddlewarePipeline(timeout=TimeoutOff())
    plan = _plan(is_async=True)
    strategy: SessionStrategy = Shared(session=_StubSession())
    result = pipeline.build_for(plan, strategy)
    types = [type(m) for m in result]
    assert types == [
        AsyncDepGuardMiddleware,
        TimeoutMiddleware,
        _SessionRunMiddleware,
    ], "Shared strategy must yield _SessionRunMiddleware (upstream session)"


def test_async_test_arrange_appends_session_run_middleware() -> None:
    """Arrange strategy appends _SessionRunMiddleware for async tests.

    Same middleware class as Shared — the scope distinction is expressed at
    the strategy level, not by having distinct middleware classes.
    """
    pipeline = _MiddlewarePipeline(timeout=TimeoutOff())
    plan = _plan(is_async=True)
    strategy: SessionStrategy = Arrange(session=_StubSession())
    result = pipeline.build_for(plan, strategy)
    types = [type(m) for m in result]
    assert types == [
        AsyncDepGuardMiddleware,
        TimeoutMiddleware,
        _SessionRunMiddleware,
    ], "Arrange strategy must yield _SessionRunMiddleware (upstream session)"


def test_timeout_set_reaches_middleware() -> None:
    """TimeoutSet is forwarded unchanged to TimeoutMiddleware during assembly."""
    pipeline = _MiddlewarePipeline(timeout=TimeoutSet(60))
    plan = _plan(is_async=False)
    strategy: SessionStrategy = Fresh(backend=AsyncioBackend())
    result = pipeline.build_for(plan, strategy)
    timeout_mw = next(m for m in result if isinstance(m, TimeoutMiddleware))
    assert isinstance(timeout_mw.timeout, TimeoutSet), (
        "TimeoutSet must reach TimeoutMiddleware unchanged"
    )
    assert timeout_mw.timeout.seconds == 60, (
        "TimeoutSet.seconds must be preserved through pipeline assembly"
    )


def test_timeout_order_is_between_post_guard_and_pre_session() -> None:
    """TimeoutMiddleware sits after guards, before session middleware."""
    pipeline = _MiddlewarePipeline(timeout=TimeoutSet(60))
    plan = _plan(is_async=True)
    strategy: SessionStrategy = Fresh(backend=AsyncioBackend())
    result = pipeline.build_for(plan, strategy)
    guard_idx = next(
        i for i, m in enumerate(result) if isinstance(m, AsyncDepGuardMiddleware)
    )
    timeout_idx = next(
        i for i, m in enumerate(result) if isinstance(m, TimeoutMiddleware)
    )
    session_idx = next(
        i for i, m in enumerate(result) if isinstance(m, AsyncBridgeMiddleware)
    )
    assert guard_idx < timeout_idx < session_idx, (
        "expected pipeline order: guard < timeout < session-middleware"
    )
