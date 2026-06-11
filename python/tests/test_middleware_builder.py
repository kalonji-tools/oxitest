"""Tests for MiddlewareBuilder ordering validation."""

from __future__ import annotations

from oxitest._bridge._middleware import (
    AsyncBridgeMiddleware,
    BareAssertMiddleware,
    MiddlewareBuilder,
    TimeoutMiddleware,
)
from oxitest._bridge._raises import raises


def test_default_pipeline_builds_without_error():
    builder = MiddlewareBuilder()
    assert builder._pipeline[0] is BareAssertMiddleware, (
        "BareAssertMiddleware must be first in default pipeline"
    )
    assert builder._pipeline[-1] is AsyncBridgeMiddleware, (
        "AsyncBridgeMiddleware must be last in default pipeline"
    )


def test_remove_bare_assert_raises():
    builder = MiddlewareBuilder()
    with raises(ValueError, match="BareAssertMiddleware cannot be removed"):
        builder.remove(BareAssertMiddleware)


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


def test_insert_before_bare_assert_raises():
    builder = MiddlewareBuilder()
    with raises(ValueError, match="Cannot insert before BareAssertMiddleware"):
        builder.insert_before(BareAssertMiddleware, TimeoutMiddleware)


def test_insert_after_async_bridge_raises():
    builder = MiddlewareBuilder()
    with raises(ValueError, match="Cannot insert after AsyncBridgeMiddleware"):
        builder.insert_after(AsyncBridgeMiddleware, TimeoutMiddleware)
