from __future__ import annotations

import asyncio

from oxitest import raises, warns
from oxitest._bridge._async_backend import AsyncioBackend, AsyncioSharedSession


def test_asyncio_backend_name():
    backend = AsyncioBackend()
    assert backend.name == "asyncio", f"expected 'asyncio', got {backend.name!r}"


def test_asyncio_backend_runs_coroutine():
    backend = AsyncioBackend()

    async def coro():
        return 42

    result = backend.run(coro())
    assert result == 42, f"expected 42, got {result!r}"


def test_asyncio_backend_propagates_exception():
    backend = AsyncioBackend()

    async def coro():
        raise ValueError("boom")

    with raises(ValueError, match="boom"):
        backend.run(coro())


def test_asyncio_backend_creates_shared_session():
    backend = AsyncioBackend()
    session = backend.create_shared_session()
    assert isinstance(session, AsyncioSharedSession), (
        f"expected AsyncioSharedSession, got {type(session).__name__}"
    )
    session.close()


def test_shared_session_runs_coroutine():
    session = AsyncioSharedSession()

    async def coro():
        return 99

    result = session.run(coro())
    assert result == 99, f"expected 99, got {result!r}"
    session.close()


def test_shared_session_close_is_idempotent():
    session = AsyncioSharedSession()
    session.close()
    session.close()  # must not raise


def test_shared_session_cleans_stray_tasks():
    session = AsyncioSharedSession()

    async def spawner():
        async def background():
            await asyncio.sleep(999)

        asyncio.ensure_future(background())  # noqa: RUF006 — intentional leak for detection test
        return "done"

    with warns(UserWarning, match="(?i)leaked"):
        result = session.run(spawner())

    assert result == "done", f"expected 'done', got {result!r}"
    session.close()
