"""Tests for async dependency guards in sync fixture contexts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

from oxitest import raises
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._fixture_instantiator import (
    _reject_async_in_sync,
    _reject_nonshared_async,
)


def test_reject_async_in_sync_raises_on_coroutine() -> None:
    """_reject_async_in_sync should raise FixtureSetupError when given a coroutine."""

    async def coro() -> None:
        pass

    c = coro()
    with raises(FixtureSetupError):
        _reject_async_in_sync("dep_a", c, "my_fixture")


def test_reject_async_in_sync_raises_on_async_gen() -> None:
    """_reject_async_in_sync raises FixtureSetupError when given an async generator."""

    async def agen() -> AsyncGenerator[int, None]:
        yield 1

    g = agen()
    with raises(FixtureSetupError):
        _reject_async_in_sync("dep_a", g, "my_fixture")
    # clean up — asyncio.run() works in CI workers (no pre-existing event loop)
    asyncio.run(g.aclose())


def test_reject_async_in_sync_passes_on_sync_value() -> None:
    """_reject_async_in_sync should not raise for plain synchronous values."""
    # Should not raise for plain values
    _reject_async_in_sync("dep_a", 42, "my_fixture")


def test_reject_nonshared_async_raises_on_coroutine() -> None:
    """_reject_nonshared_async raises FixtureSetupError mentioning lifetime mismatch."""

    async def coro() -> None:
        pass

    c = coro()
    with raises(FixtureSetupError) as exc_info:
        _reject_nonshared_async("dep_a", c, "shared_fix")
    assert "lifetime mismatch" in str(exc_info.value), (
        f"error should mention lifetime mismatch, got {exc_info.value!r}"
    )


def test_reject_nonshared_async_passes_on_sync_value() -> None:
    """_reject_nonshared_async should not raise for plain synchronous values."""
    _reject_nonshared_async("dep_a", "hello", "shared_fix")
