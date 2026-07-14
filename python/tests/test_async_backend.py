"""Tests for AsyncioBackend and AsyncioSharedSession event-loop lifecycle."""

from __future__ import annotations

import asyncio
from typing import Never

from oxitest import Fixture, raises
from oxitest._bridge._async_backend import AsyncioBackend, AsyncioSharedSession
from oxitest._bridge.result import Diagnostic


def test_asyncio_backend_name() -> None:
    """AsyncioBackend.name should identify the backend as 'asyncio'."""
    backend = AsyncioBackend()
    assert backend.name == "asyncio", f"expected 'asyncio', got {backend.name!r}"


def test_asyncio_backend_runs_coroutine() -> None:
    """AsyncioBackend.run() should execute a coroutine and return its result."""
    backend = AsyncioBackend()

    async def coro() -> int:
        return 42

    result = backend.run(coro())
    assert result == 42, f"expected 42, got {result!r}"


def test_asyncio_backend_propagates_exception() -> None:
    """AsyncioBackend.run() should propagate exceptions raised inside the coroutine."""
    backend = AsyncioBackend()

    async def coro() -> Never:
        msg = "boom"
        raise ValueError(msg)

    with raises(ValueError, match="boom"):
        backend.run(coro())


def test_asyncio_backend_creates_shared_session() -> None:
    """create_shared_session() should return an AsyncioSharedSession instance."""
    backend = AsyncioBackend()
    session = backend.create_shared_session()
    assert isinstance(session, AsyncioSharedSession), (
        f"expected AsyncioSharedSession, got {type(session).__name__}"
    )
    session.close()


def test_shared_session_runs_coroutine() -> None:
    """AsyncioSharedSession.run() should execute a coroutine and return its result."""
    session = AsyncioSharedSession()

    async def coro() -> int:
        return 99

    result = session.run(coro())
    assert result == 99, f"expected 99, got {result!r}"
    session.close()


def test_shared_session_close_is_idempotent() -> None:
    """Calling close() twice on a shared session should not raise."""
    session = AsyncioSharedSession()
    session.close()
    session.close()  # must not raise


def test_shared_session_cleans_stray_tasks(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """run() should detect and emit a diagnostic about tasks that were leaked."""
    session = AsyncioSharedSession()

    async def spawner() -> str:
        async def background() -> None:
            await asyncio.sleep(999)

        asyncio.ensure_future(background())  # noqa: RUF006 — intentional leak for detection test
        return "done"

    result = session.run(spawner())

    assert result == "done", f"expected 'done', got {result!r}"
    assert any(
        d.context == "async session" and "leaked" in d.message.lower()
        for d in diag_collector
    ), (
        "leaked tasks must surface as diagnostics so developers know to await or cancel"
        f" them: {diag_collector}"
    )
    session.close()
