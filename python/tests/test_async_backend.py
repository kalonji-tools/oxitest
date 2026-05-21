from __future__ import annotations

from oxitest._bridge._async_backend import AsyncioBackend


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

    try:
        backend.run(coro())
        assert False, "expected ValueError"  # noqa: PT015
    except ValueError as exc:
        assert "boom" in str(exc), f"expected 'boom' in error, got {exc!r}"
