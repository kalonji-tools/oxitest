"""Tests for MiddlewareBuilder ordering validation."""

from __future__ import annotations

from oxitest import raises
from oxitest._bridge._middleware import (
    AsyncBridgeMiddleware,
    AsyncDepGuardMiddleware,
    MiddlewareBuilder,
    TimeoutMiddleware,
)


def test_default_pipeline_builds_without_error() -> None:
    """Default pipeline: AsyncDepGuardMiddleware first, AsyncBridgeMiddleware last."""
    builder = MiddlewareBuilder()
    assert builder.pipeline[0] is AsyncDepGuardMiddleware, (
        "AsyncDepGuardMiddleware must be first in default pipeline"
    )
    assert builder.pipeline[-1] is AsyncBridgeMiddleware, (
        "AsyncBridgeMiddleware must be last in default pipeline"
    )


def test_remove_async_dep_guard_raises() -> None:
    """Removing the pinned AsyncDepGuardMiddleware raises ValueError."""
    builder = MiddlewareBuilder()
    with raises(ValueError, match="AsyncDepGuardMiddleware cannot be removed"):
        builder.remove(AsyncDepGuardMiddleware)


def test_remove_async_bridge_raises() -> None:
    """Attempting to remove the pinned AsyncBridgeMiddleware should raise ValueError."""
    builder = MiddlewareBuilder()
    with raises(ValueError, match="AsyncBridgeMiddleware cannot be removed"):
        builder.remove(AsyncBridgeMiddleware)


def test_remove_non_pinned_middleware_succeeds() -> None:
    """Removing a non-pinned middleware like TimeoutMiddleware should succeed."""
    builder = MiddlewareBuilder()
    builder.remove(TimeoutMiddleware)
    assert TimeoutMiddleware not in builder.pipeline, (
        "TimeoutMiddleware should be removed from pipeline"
    )


def test_insert_before_async_dep_guard_raises() -> None:
    """Inserting before the first pinned position should raise ValueError."""
    builder = MiddlewareBuilder()
    with raises(ValueError, match="Cannot insert before AsyncDepGuardMiddleware"):
        builder.insert_before(AsyncDepGuardMiddleware, TimeoutMiddleware)


def test_insert_after_async_bridge_raises() -> None:
    """Inserting after the last pinned position should raise ValueError."""
    builder = MiddlewareBuilder()
    with raises(ValueError, match="Cannot insert after AsyncBridgeMiddleware"):
        builder.insert_after(AsyncBridgeMiddleware, TimeoutMiddleware)
