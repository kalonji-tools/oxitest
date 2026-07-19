"""Tests for acquire_session_guarded — nested-acquire policy enforcement.

The guard is the framework's single seam for acquiring async sessions from a
backend. It defaults to strict: nested acquire raises ``RuntimeError`` unless
the backend opts in via ``supports_nested_acquire = True``. These tests pin
that policy and the ``ContextVar`` cleanup on exit.
"""

from __future__ import annotations

from collections.abc import Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

import oxitest as oxi
from oxitest._bridge._async_backend import AsyncSession
from oxitest._bridge._async_session_guard import acquire_session_guarded


def _exhaust_coro(coro: Coroutine[Any, Any, Any]) -> Any:
    """Drive a coroutine to completion in a single send (single-step only)."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    msg = "coroutine did not complete in one step"
    raise RuntimeError(msg)


class _StubSession:
    """Minimal AsyncSession — single-step exhaustion, no real event loop."""

    def run(self, coro: Coroutine[Any, Any, Any], /) -> Any:
        return _exhaust_coro(coro)


class _StrictBackend:
    """Backend that does not allow nested acquire (default policy)."""

    name = "strict-stub"
    supports_nested_acquire = False

    @contextmanager
    def acquire_session(self) -> Iterator[AsyncSession]:
        yield _StubSession()


class _NestingBackend:
    """Backend that opts in to nested acquire (backend author knows best)."""

    name = "nesting-stub"
    supports_nested_acquire = True

    @contextmanager
    def acquire_session(self) -> Iterator[AsyncSession]:
        yield _StubSession()


def test_nested_acquire_raises_on_strict_backend() -> None:
    """Guard must raise RuntimeError naming the backend when nesting is forbidden.

    Backend name in the message is what turns a "nested acquire" mystery into
    an actionable fix — otherwise users see a bare RuntimeError and grep
    randomly for the source.
    """
    backend = _StrictBackend()
    with (
        oxi.raises(RuntimeError, match="strict-stub"),
        acquire_session_guarded(backend) as _outer,
        acquire_session_guarded(backend) as _inner,
    ):
        # unreachable — the second acquire must raise
        pass


def test_nested_acquire_allowed_on_opt_out_backend() -> None:
    """A backend declaring ``supports_nested_acquire = True`` may nest freely.

    Framework does not dictate policy: if the backend author says their runtime
    handles nested acquires safely, the guard steps out of the way.
    """
    backend = _NestingBackend()

    async def one() -> int:
        return 1

    async def two() -> int:
        return 2

    with (
        acquire_session_guarded(backend) as outer,
        acquire_session_guarded(backend) as inner,
    ):
        outer_result = outer.run(one())
        inner_result = inner.run(two())

    assert outer_result == 1, f"outer run must succeed, got {outer_result!r}"
    assert inner_result == 2, f"inner run must succeed, got {inner_result!r}"


def test_session_open_contextvar_resets_after_exit() -> None:
    """After the guard exits, a fresh acquire must succeed.

    Sequential acquires (not nested) are the normal case — the ContextVar
    that tracks "session open" must reset on exit so the next acquire is
    not falsely detected as nested.
    """
    backend = _StrictBackend()

    async def first_value() -> int:
        return 1

    with acquire_session_guarded(backend) as first:
        first.run(first_value())

    async def second_value() -> str:
        return "second"

    # after exit, second acquire must work
    with acquire_session_guarded(backend) as second:
        result = second.run(second_value())

    assert result == "second", (
        "sequential acquires must succeed — ContextVar cleanup is what"
        f" separates 'nested' from 'sequential', got {result!r}"
    )


def test_guard_resets_contextvar_when_backend_acquire_raises() -> None:
    """If the backend's own acquire raises, the guard must still reset state.

    Otherwise a single failed acquire would poison the ContextVar for the rest
    of the process, making every subsequent acquire look nested.
    """

    class _BrokenBackend:
        name = "broken"
        supports_nested_acquire = False

        @contextmanager
        def acquire_session(self) -> Iterator[AsyncSession]:
            msg = "backend cannot start"
            raise RuntimeError(msg)
            yield  # pragma: no cover — unreachable

    backend: Any = _BrokenBackend()
    with (
        oxi.raises(RuntimeError, match="cannot start"),
        acquire_session_guarded(backend),
    ):
        pass  # pragma: no cover — unreachable

    # after the failure, a healthy acquire must succeed — ContextVar was reset
    healthy = _StrictBackend()
    with acquire_session_guarded(healthy) as session:

        async def value() -> str:
            return "healthy"

        result = session.run(value())

    assert result == "healthy", (
        "healthy acquire after a failed one must succeed — a leaked True in"
        " _SESSION_OPEN would falsely reject this as nested"
    )


def test_guard_resets_contextvar_when_body_raises() -> None:
    """When the guarded body raises, the guard must still reset _SESSION_OPEN.

    Framework call sites bubble exceptions; a leaked True would break every
    subsequent test in the same process by making them look nested.
    """
    backend = _StrictBackend()
    body_error = ValueError("body error")

    with (
        oxi.raises(ValueError, match="body error"),
        acquire_session_guarded(backend),
    ):
        raise body_error

    # After failure, a fresh strict acquire must succeed.
    with acquire_session_guarded(backend) as session:

        async def value() -> str:
            return "ok"

        assert session.run(value()) == "ok", (
            "post-failure acquire must not see stale _SESSION_OPEN = True"
        )
