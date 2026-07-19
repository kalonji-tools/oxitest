"""Tests for AsyncioBackend and AsyncioSession — the acquire_session seam."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Never

from oxitest import Fixture, raises
from oxitest._bridge._async_backend import (
    AsyncioBackend,
    AsyncioSession,
)
from oxitest._bridge.result import Diagnostic


def test_asyncio_backend_name() -> None:
    """AsyncioBackend.name should identify the backend as 'asyncio'."""
    backend = AsyncioBackend()
    assert backend.name == "asyncio", f"expected 'asyncio', got {backend.name!r}"


def test_asyncio_backend_supports_nested_acquire_defaults_false() -> None:
    """Asyncio nested acquire raises at runtime — default must be strict False."""
    backend = AsyncioBackend()
    assert backend.supports_nested_acquire is False, (
        "asyncio raises on nested asyncio.run — backend must default to strict False"
        " so the framework guard rejects nesting before runtime does"
    )


def test_acquire_session_yields_asyncio_session() -> None:
    """acquire_session() must yield an AsyncioSession the framework can drive."""
    backend = AsyncioBackend()
    with backend.acquire_session() as session:
        assert isinstance(session, AsyncioSession), (
            f"expected AsyncioSession, got {type(session).__name__}"
            " — the framework relies on the concrete type for asyncgen finalization"
        )


def test_asyncio_session_runs_coroutine() -> None:
    """AsyncioSession.run must execute a coroutine and return its result."""
    with AsyncioSession() as session:

        async def coro() -> int:
            return 42

        result = session.run(coro())

    assert result == 42, f"expected 42, got {result!r}"


def test_asyncio_session_propagates_exception() -> None:
    """AsyncioSession.run must propagate exceptions from the coroutine."""
    with AsyncioSession() as session:

        async def coro() -> Never:
            msg = "boom"
            raise ValueError(msg)

        with raises(ValueError, match="boom"):
            session.run(coro())


def test_asyncio_session_multiple_run_calls_share_runtime() -> None:
    """Multiple .run calls on one session must share a single event loop.

    Session identity of the runtime is the whole reason for the shape: an
    asyncgen yielded from one run stays finalizable on __exit__, so a shared
    fixture set up on call A can be torn down on call B — impossible if each
    run spun up its own loop.
    """
    loops: list[asyncio.AbstractEventLoop] = []

    async def capture_loop() -> None:
        loops.append(asyncio.get_running_loop())

    with AsyncioSession() as session:
        session.run(capture_loop())
        session.run(capture_loop())

    assert len(loops) == 2, f"expected two run calls, got {len(loops)}"
    assert loops[0] is loops[1], (
        "multiple .run calls must share the same event loop — asyncgen"
        " finalization at __exit__ depends on it"
    )


def test_asyncio_session_finalizes_asyncgens_on_exit() -> None:
    """Asyncgen shutdown runs on __exit__, not per-run.

    The pre-refactor code called asyncio.run per session.run, which finalized
    every asyncgen on each call. The new shape defers finalization to session
    exit, so setup-yield-teardown across two run calls works.
    """
    torn_down: list[bool] = []

    async def _gen() -> AsyncGenerator[int, None]:
        try:
            yield 1
        finally:
            torn_down.append(True)

    session = AsyncioSession()
    with session:
        gen = _gen()

        async def _prime() -> int:
            return await anext(gen)

        value = session.run(_prime())
        assert value == 1, f"expected 1 from asyncgen, got {value!r}"
        assert torn_down == [], (
            "asyncgen must not be finalized while session is still open —"
            " pre-refactor asyncio.run() closed it too early"
        )

    assert torn_down == [True], (
        "asyncgen finalize must run on session __exit__ (loop.shutdown_asyncgens)"
        " — this is the whole reason for the refactor"
    )


def test_asyncio_session_run_after_exit_raises() -> None:
    """AsyncioSession.run after __exit__ must raise a clear RuntimeError.

    A muddled error here (e.g. AttributeError on ``None._loop``) makes bugs
    in fixture-manager cleanup order hard to trace.
    """
    session = AsyncioSession()
    with session:
        pass

    async def coro() -> int:
        return 1

    dead = coro()
    try:
        with raises(RuntimeError, match="already exited"):
            session.run(dead)
    finally:
        dead.close()  # avoid coroutine-never-awaited warning


def test_asyncio_session_double_exit_is_idempotent() -> None:
    """Exiting an AsyncioSession twice must not raise (cleanup guard)."""
    session = AsyncioSession()
    session.__exit__(None, None, None)
    session.__exit__(None, None, None)  # must not raise


def test_asyncio_session_stray_task_diagnostic(
    diag_collector: Fixture[list[Diagnostic]],
) -> None:
    """Every session — not just shared ones — emits a diagnostic on stray tasks.

    Any test that spawns a task and forgets to await it is a bug regardless of
    whether the session lives long. Pre-refactor this only fired for the
    shared session; universal detection catches leaks in every code path.
    """
    with AsyncioSession() as session:

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
        "leaked tasks must surface as diagnostics so developers know to await"
        f" or cancel them: {diag_collector}"
    )
