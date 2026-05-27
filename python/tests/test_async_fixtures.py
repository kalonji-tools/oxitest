"""Tests for SharedAsyncManager — extracted async fixture lifecycle management."""

from __future__ import annotations

from unittest.mock import MagicMock

from oxitest._bridge._async_backend import AsyncioBackend
from oxitest._bridge._fixture_session import SharedAsyncManager

# ── Stub backend / session ────────────────────────────────────────────────────


def _make_stub_backend():
    """Return a mock AsyncBackend whose shared session tracks run() calls."""
    backend = MagicMock(spec=["create_shared_session", "name"])
    backend.name = "stub"
    session = MagicMock(spec=["run", "close"])
    session.run.side_effect = lambda coro: _exhaust_coro(coro)
    backend.create_shared_session.return_value = session
    return backend, session


def _exhaust_coro(coro):
    """Synchronously exhaust a coroutine (single-step only)."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise RuntimeError("coroutine did not complete in one step")


# ── Initial state ─────────────────────────────────────────────────────────────


def test_initial_state_session_is_none():
    mgr = SharedAsyncManager(AsyncioBackend())

    assert mgr.session is None, "session should be None before any resolve"


def test_initial_state_was_used_is_false():
    mgr = SharedAsyncManager(AsyncioBackend())

    assert mgr.was_used is False, "was_used should be False initially"


# ── was_used flag ─────────────────────────────────────────────────────────────


def test_was_used_setter():
    mgr = SharedAsyncManager(AsyncioBackend())

    mgr.was_used = True

    assert mgr.was_used is True, "was_used should be True after setting"


def test_was_used_can_be_reset():
    mgr = SharedAsyncManager(AsyncioBackend())
    mgr.was_used = True

    mgr.was_used = False

    assert mgr.was_used is False, "was_used should be False after reset"


# ── resolve ───────────────────────────────────────────────────────────────────


def test_resolve_creates_session_lazily():
    backend, session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    assert mgr.session is None, "session should be None before resolve"

    async def my_fixture():
        return 42

    value = mgr.resolve(my_fixture, {})

    assert mgr.session is session, "session should be created on first resolve"
    assert value == 42, "resolved value should be 42"
    backend.create_shared_session.assert_called_once()


def test_resolve_reuses_existing_session():
    backend, _session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    async def fx_a():
        return "a"

    async def fx_b():
        return "b"

    mgr.resolve(fx_a, {})
    mgr.resolve(fx_b, {})

    backend.create_shared_session.assert_called_once()


def test_resolve_sets_was_used():
    backend, _ = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    async def my_fixture():
        return 1

    mgr.resolve(my_fixture, {})

    assert mgr.was_used is True, "was_used should be True after resolve"


def test_resolve_passes_deps_to_fixture():
    backend, _session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)
    received = {}

    async def my_fixture(a: int = 0, b: str = ""):  # noqa: D103
        received["a"] = a
        received["b"] = b
        return "ok"

    mgr.resolve(my_fixture, {"a": 10, "b": "hello"})

    assert received == {"a": 10, "b": "hello"}, "deps should be forwarded"


def test_resolve_async_generator_tracks_teardown():
    """Async generator fixtures have their teardown tracked for cleanup."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)
    torn_down = []

    async def my_fixture():
        yield 99
        torn_down.append(True)

    value = mgr.resolve(my_fixture, {})

    assert value == 99, "should yield the first value"
    assert len(mgr._teardowns) == 1, "should track one teardown"

    mgr.cleanup()

    assert torn_down == [True], "teardown should have run"


def test_resolve_plain_coroutine():
    """Plain async def (not generator) returns the value directly."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    async def my_fixture():
        return "hello"

    value = mgr.resolve(my_fixture, {})

    assert value == "hello", "should return the awaited value"
    assert len(mgr._teardowns) == 0, "no teardowns for plain coroutines"


def test_resolve_sync_function_passthrough():
    """If a fixture func returns a sync value, pass it through."""
    backend, _ = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    def my_fixture():
        return "sync_val"

    value = mgr.resolve(my_fixture, {})

    assert value == "sync_val", "should return sync value directly"


# ── cleanup ───────────────────────────────────────────────────────────────────


def test_cleanup_closes_session():
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    async def my_fixture():
        return 1

    mgr.resolve(my_fixture, {})
    assert mgr.session is not None, "session should exist after resolve"

    mgr.cleanup()

    assert mgr.session is None, "session should be None after cleanup"


def test_cleanup_noop_without_session():
    """Cleanup when no async fixtures were resolved should not raise."""
    mgr = SharedAsyncManager(AsyncioBackend())

    mgr.cleanup()  # should not raise

    assert mgr.session is None, "session should remain None"


def test_cleanup_drains_teardowns_in_reverse():
    """Teardowns run in LIFO order."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)
    order: list[str] = []

    async def fx_a():
        yield "a"
        order.append("a_teardown")

    async def fx_b():
        yield "b"
        order.append("b_teardown")

    mgr.resolve(fx_a, {})
    mgr.resolve(fx_b, {})

    mgr.cleanup()

    assert order == ["b_teardown", "a_teardown"], "teardowns should run LIFO"


# ── backend property ──────────────────────────────────────────────────────────


def test_backend_property():
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    assert mgr.backend is backend, "should expose the backend"


# ── error handling ────────────────────────────────────────────────────────────


def test_resolve_raises_fixture_setup_error_on_exception():
    """resolve() wraps exceptions in FixtureSetupError."""
    import oxitest as oxi
    from oxitest._bridge._errors import FixtureSetupError

    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    async def bad_fixture():
        msg = "boom"
        raise ValueError(msg)

    with oxi.raises(FixtureSetupError):
        mgr.resolve(bad_fixture, {})
