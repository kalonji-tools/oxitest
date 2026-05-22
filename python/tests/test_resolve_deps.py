from __future__ import annotations

import asyncio

from oxitest import raises
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._fixture_session import (
    _reject_async_in_sync,
    _reject_nonshared_async,
)


def test_reject_async_in_sync_raises_on_coroutine():
    async def coro():
        pass

    c = coro()
    with raises(FixtureSetupError):
        _reject_async_in_sync("dep_a", c, "my_fixture")


def test_reject_async_in_sync_raises_on_async_gen():
    async def agen():
        yield 1

    g = agen()
    with raises(FixtureSetupError):
        _reject_async_in_sync("dep_a", g, "my_fixture")
    # clean up — asyncio.run() works in CI workers (no pre-existing event loop)
    asyncio.run(g.aclose())


def test_reject_async_in_sync_passes_on_sync_value():
    # Should not raise for plain values
    _reject_async_in_sync("dep_a", 42, "my_fixture")


def test_reject_nonshared_async_raises_on_coroutine():
    async def coro():
        pass

    c = coro()
    with raises(FixtureSetupError) as exc_info:
        _reject_nonshared_async("dep_a", c, "shared_fix")
    assert "lifetime mismatch" in str(exc_info.value), (
        f"error should mention lifetime mismatch, got {exc_info.value!r}"
    )


def test_reject_nonshared_async_passes_on_sync_value():
    _reject_nonshared_async("dep_a", "hello", "shared_fix")
