"""Tests for SharedAsyncManager — extracted async fixture lifecycle management."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

import oxitest as oxi
from oxitest._bridge._async_backend import AsyncioBackend, AsyncSession
from oxitest._bridge._async_orchestrator import SharedAsyncManager
from oxitest._bridge._diagnostic_collector import _diagnostic_collector_var
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge.result import Diagnostic

# ── Stub backend / session ────────────────────────────────────────────────────


def _exhaust_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive a coroutine to completion in a single send (single-step only)."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    msg = "coroutine did not complete in one step"
    raise RuntimeError(msg)


class _StubSession:
    """Minimal AsyncSession that synchronously exhausts coroutines.

    No real event loop — tests using this stub only exercise dispatch logic,
    not asyncio semantics. Real loop lifecycle is covered in
    ``test_async_backend.py``.
    """

    def __init__(self) -> None:
        self.run_count = 0
        self.exited = False

    def run(self, coro: Coroutine[Any, Any, Any], /) -> Any:
        self.run_count += 1
        return _exhaust_coro(coro)


class _StubBackend:
    """Minimal AsyncBackend yielding a _StubSession via acquire_session."""

    name = "stub"
    supports_nested_acquire = False

    def __init__(self) -> None:
        self.session = _StubSession()
        self.acquire_count = 0

    @contextmanager
    def acquire_session(self) -> Iterator[AsyncSession]:
        self.acquire_count += 1
        try:
            yield self.session
        finally:
            self.session.exited = True


def _make_stub_backend() -> tuple[_StubBackend, _StubSession]:
    """Return a stub AsyncBackend and its shared session."""
    backend = _StubBackend()
    session = backend.session
    return backend, session


# ── Initial state ─────────────────────────────────────────────────────────────


def test_initial_state_session_is_none() -> None:
    """SharedAsyncManager starts with no session before any fixture is resolved."""
    mgr = SharedAsyncManager(AsyncioBackend())

    assert mgr.session is None, "session should be None before any resolve"


def test_initial_state_was_used_is_false() -> None:
    """was_used is False on a freshly created manager before any resolve call."""
    mgr = SharedAsyncManager(AsyncioBackend())

    assert mgr.was_used is False, "was_used should be False initially"


# ── was_used flag ─────────────────────────────────────────────────────────────


def test_was_used_setter() -> None:
    """was_used can be set to True to mark the manager as having run async fixtures."""
    mgr = SharedAsyncManager(AsyncioBackend())

    mgr.was_used = True

    assert mgr.was_used is True, "was_used should be True after setting"


def test_was_used_can_be_reset() -> None:
    """was_used can be reset to False after being set, supporting session reuse."""
    mgr = SharedAsyncManager(AsyncioBackend())
    mgr.was_used = True

    mgr.was_used = False

    assert mgr.was_used is False, "was_used should be False after reset"


# ── resolve ───────────────────────────────────────────────────────────────────


def test_resolve_creates_session_lazily() -> None:
    """The shared session is acquired on first resolve, not at construction time.

    Lazy acquisition matters because tests with no async fixtures should not
    pay for event-loop creation or asyncgen finalization overhead.
    """
    backend, session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    assert mgr.session is None, "session should be None before resolve"

    async def my_fixture() -> int:
        return 42

    value = mgr.resolve(my_fixture, {})

    assert mgr.session is session, "session should be acquired on first resolve"
    assert value == 42, "resolved value should be 42"
    assert backend.acquire_count == 1, (
        "backend.acquire_session must be called exactly once — repeated"
        " acquires would drop asyncgen finalization on the earlier session"
    )


def test_resolve_reuses_existing_session() -> None:
    """Subsequent resolve calls reuse the acquired session, not a fresh one.

    Reuse guarantees that cross-fixture async state (e.g. shared connection
    pools) lives on one runtime so teardown at cleanup can drain it.
    """
    backend, _session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    async def fx_a() -> str:
        return "a"

    async def fx_b() -> str:
        return "b"

    mgr.resolve(fx_a, {})
    mgr.resolve(fx_b, {})

    assert backend.acquire_count == 1, (
        "backend.acquire_session must be called exactly once — a second"
        " acquire would leak the first session and break asyncgen finalization"
    )


def test_resolve_sets_was_used() -> None:
    """Resolving a fixture marks the manager as used so cleanup runs at session end."""
    backend, _ = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    async def my_fixture() -> int:
        return 1

    mgr.resolve(my_fixture, {})

    assert mgr.was_used is True, "was_used should be True after resolve"


def test_resolve_passes_deps_to_fixture() -> None:
    """Dependency values are forwarded as keyword arguments to the fixture."""
    backend, _session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)
    received = {}

    async def my_fixture(a: int = 0, b: str = "") -> str:
        received["a"] = a
        received["b"] = b
        return "ok"

    mgr.resolve(my_fixture, {"a": 10, "b": "hello"})

    assert received == {"a": 10, "b": "hello"}, "deps should be forwarded"


def test_resolve_async_generator_tracks_teardown() -> None:
    """Async generator fixtures have their teardown tracked for cleanup."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)
    torn_down = []

    async def my_fixture() -> AsyncGenerator[int, None]:
        yield 99
        torn_down.append(True)

    value = mgr.resolve(my_fixture, {})

    assert value == 99, "should yield the first value"
    assert len(mgr.teardowns) == 1, "should track one teardown"

    mgr.cleanup()

    assert torn_down == [True], "teardown should have run"


def test_resolve_plain_coroutine() -> None:
    """Plain async def (not generator) returns the value directly."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    async def my_fixture() -> str:
        return "hello"

    value = mgr.resolve(my_fixture, {})

    assert value == "hello", "should return the awaited value"
    assert len(mgr.teardowns) == 0, "no teardowns for plain coroutines"


def test_resolve_sync_function_passthrough() -> None:
    """If a fixture func returns a sync value, pass it through."""
    backend, _ = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    def my_fixture() -> str:
        return "sync_val"

    value = mgr.resolve(my_fixture, {})

    assert value == "sync_val", "should return sync value directly"


# ── cleanup ───────────────────────────────────────────────────────────────────


def test_cleanup_closes_session() -> None:
    """cleanup() closes the shared async session and sets it to None."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    async def my_fixture() -> int:
        return 1

    mgr.resolve(my_fixture, {})
    assert mgr.session is not None, "session should exist after resolve"

    mgr.cleanup()

    assert mgr.session is None, "session should be None after cleanup"


def test_cleanup_noop_without_session() -> None:
    """Cleanup when no async fixtures were resolved should not raise."""
    mgr = SharedAsyncManager(AsyncioBackend())

    mgr.cleanup()  # should not raise

    assert mgr.session is None, "session should remain None"


def test_cleanup_drains_teardowns_in_reverse() -> None:
    """Teardowns run in LIFO order."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)
    order: list[str] = []

    async def fx_a() -> AsyncGenerator[str, None]:
        yield "a"
        order.append("a_teardown")

    async def fx_b() -> AsyncGenerator[str, None]:
        yield "b"
        order.append("b_teardown")

    mgr.resolve(fx_a, {})
    mgr.resolve(fx_b, {})

    mgr.cleanup()

    assert order == ["b_teardown", "a_teardown"], "teardowns should run LIFO"


# ── backend property ──────────────────────────────────────────────────────────


def test_backend_property() -> None:
    """The backend property returns the AsyncBackend passed at construction time."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    assert mgr.backend is backend, "should expose the backend"


# ── error handling ────────────────────────────────────────────────────────────


def test_resolve_raises_fixture_setup_error_on_exception() -> None:
    """resolve() wraps exceptions in FixtureSetupError.

    Cleanup runs unconditionally even after a failed resolve: the manager
    holds a session/stack the moment the first resolve enters the guard, and
    dropping the manager without ``cleanup()`` would leak both the event loop
    and the guard's nested-acquire tracking.
    """
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    async def bad_fixture() -> None:
        msg = "boom"
        raise ValueError(msg)

    try:
        with oxi.raises(FixtureSetupError):
            mgr.resolve(bad_fixture, {})
    finally:
        mgr.cleanup()


def test_async_generator_fixture_teardown_exception_reported() -> None:
    """Async fixture raising during teardown should report the error."""
    backend = AsyncioBackend()
    mgr = SharedAsyncManager(backend)

    async def bad_teardown() -> AsyncGenerator[int, None]:
        yield 42
        msg = "teardown exploded"
        raise RuntimeError(msg)

    value = mgr.resolve(bad_teardown, {})

    assert value == 42, f"should yield the value before teardown, got {value!r}"
    assert len(mgr.teardowns) == 1, "should track one teardown"

    diags: list[Diagnostic] = []
    token = _diagnostic_collector_var.set(diags)
    try:
        mgr.cleanup()
    finally:
        _diagnostic_collector_var.reset(token)

    assert any(
        d.context == "fixture teardown" and "teardown exploded" in d.message
        for d in diags
    ), (
        "async fixture teardown errors must emit a diagnostic so users know cleanup"
        " failed -- silently swallowing them hides resource leaks"
    )


# ── ExitStack lifetime ────────────────────────────────────────────────────────


def test_shared_async_manager_holds_session_via_exitstack() -> None:
    """One session lives across every resolve; cleanup drives its __exit__ once.

    The whole reason the manager owns an ExitStack is to keep asyncgen
    finalization deferred until end-of-session — driving __exit__ on every
    resolve would defeat the refactor. This test pins that the stack is
    responsible for exiting the session exactly once.
    """
    backend, session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    async def fx_a() -> str:
        return "a"

    async def fx_b() -> str:
        return "b"

    mgr.resolve(fx_a, {})
    mgr.resolve(fx_b, {})

    assert backend.acquire_count == 1, (
        f"exactly one acquire across resolves, got {backend.acquire_count}"
    )
    assert not session.exited, (
        "session must stay open across resolves — asyncgen finalization at"
        " session __exit__ depends on the stack keeping it open"
    )

    mgr.cleanup()

    assert session.exited, (
        "cleanup must close the ExitStack, which drives session __exit__ —"
        " otherwise loops/nurseries leak and asyncgens never finalize"
    )
    assert mgr.session is None, (
        "session slot must clear after cleanup so a second cleanup is a no-op"
    )


def test_shared_async_manager_cleanup_is_idempotent() -> None:
    """A second cleanup must not re-drive the stack — session is already gone."""
    backend, session = _make_stub_backend()
    mgr = SharedAsyncManager(backend)

    async def fx() -> str:
        return "x"

    mgr.resolve(fx, {})
    mgr.cleanup()
    mgr.cleanup()  # must not raise

    assert session.exited, "first cleanup must have exited the session"
    assert mgr.session is None, "second cleanup must remain a no-op"
