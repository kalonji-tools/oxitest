"""Tests for MiddlewareBuilder ordering validation."""

from __future__ import annotations

from oxitest._bridge._middleware import (
    AsyncBridgeMiddleware,
    AsyncDepGuardMiddleware,
    MiddlewareBuilder,
    TimeoutMiddleware,
)
from oxitest._bridge._raises import raises


def test_default_pipeline_builds_without_error():
    builder = MiddlewareBuilder()
    assert builder._pipeline[0] is AsyncDepGuardMiddleware, (
        "AsyncDepGuardMiddleware must be first in default pipeline"
    )
    assert builder._pipeline[-1] is AsyncBridgeMiddleware, (
        "AsyncBridgeMiddleware must be last in default pipeline"
    )


def test_remove_async_dep_guard_raises():
    builder = MiddlewareBuilder()
    with raises(ValueError, match="AsyncDepGuardMiddleware cannot be removed"):
        builder.remove(AsyncDepGuardMiddleware)


def test_remove_async_bridge_raises():
    builder = MiddlewareBuilder()
    with raises(ValueError, match="AsyncBridgeMiddleware cannot be removed"):
        builder.remove(AsyncBridgeMiddleware)


def test_remove_non_pinned_middleware_succeeds():
    builder = MiddlewareBuilder()
    builder.remove(TimeoutMiddleware)
    assert TimeoutMiddleware not in builder._pipeline, (
        "TimeoutMiddleware should be removed from pipeline"
    )


def test_insert_before_async_dep_guard_raises():
    builder = MiddlewareBuilder()
    with raises(ValueError, match="Cannot insert before AsyncDepGuardMiddleware"):
        builder.insert_before(AsyncDepGuardMiddleware, TimeoutMiddleware)


def test_insert_after_async_bridge_raises():
    builder = MiddlewareBuilder()
    with raises(ValueError, match="Cannot insert after AsyncBridgeMiddleware"):
        builder.insert_after(AsyncBridgeMiddleware, TimeoutMiddleware)
