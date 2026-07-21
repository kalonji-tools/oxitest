"""Async fixture orchestration — single source of truth for async fixture lifecycle.

Consolidates SharedAsyncManager (session creation, resolution, teardown) and
async dependency policy functions (_check_async_dep, _reject_async_in_sync,
_reject_nonshared_async) that were previously spread across _fixture_session.py
and _fixture_instantiator.py.
"""

from __future__ import annotations

__all__ = [
    "AsyncPolicy",
    "SharedAsyncManager",
    "_Idle",
    "_Live",
    "_check_async_dep",
    "_reject_async_in_sync",
    "_reject_nonshared_async",
]

import contextlib
import inspect
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import (
        AsyncBackend,
        AsyncSession,
    )
from oxitest._bridge._boundary import safe_teardown
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._fixture_context import _warn_teardown

# ── State variants for lazy session acquisition (ADR-0007 Rule 4) ────────────


@dataclass(frozen=True, slots=True)
class _Idle:
    pass


@dataclass(frozen=True, slots=True)
class _Live:
    session: AsyncSession


_SessionState = _Idle | _Live

# ── Async dependency policy functions ─────────────────────────────────────────


def _check_async_dep(_dep_name: str, dep_val: Any, fixture_name: str, msg: str) -> None:
    """Reject an async dependency value with a descriptive error message."""
    if inspect.iscoroutine(dep_val) or inspect.isasyncgen(dep_val):
        if inspect.iscoroutine(dep_val):
            dep_val.close()
        raise FixtureSetupError(fixture_name, RuntimeError(msg))


def _reject_async_in_sync(dep_name: str, dep_val: Any, fixture_name: str) -> None:
    """Sync fixtures cannot depend on async fixtures."""
    _check_async_dep(
        dep_name,
        dep_val,
        fixture_name,
        f"sync fixture '{fixture_name}' cannot depend on async fixture '{dep_name}'",
    )


def _reject_nonshared_async(dep_name: str, dep_val: Any, fixture_name: str) -> None:
    """Shared fixtures cannot depend on non-shared async fixtures."""
    _check_async_dep(
        dep_name,
        dep_val,
        fixture_name,
        f"shared fixture '{fixture_name}' cannot depend on "
        f"non-shared async fixture '{dep_name}' — "
        f"lifetime mismatch",
    )


AsyncPolicy = Callable[[str, Any, str], None]


# ── SharedAsyncManager ────────────────────────────────────────────────────────


class SharedAsyncManager:
    """Manages shared async fixture lifecycle: session creation, resolution, teardown.

    Extracted from FixtureSession to isolate the async fixture management concern.
    The manager lazily acquires an :class:`AsyncSession` on the first
    :meth:`resolve` call by pushing ``acquire_session_guarded(backend)`` onto an
    :class:`~contextlib.ExitStack`; the stack's ``close`` finalizes the session
    (asyncgen shutdown, loop close) on :meth:`cleanup`. Teardowns are tracked
    and drained in LIFO order before the session closes.
    """

    def __init__(self, async_backend: AsyncBackend) -> None:
        self._backend = async_backend
        self._state: _SessionState = _Idle()
        self._stack: ExitStack = ExitStack()
        self._teardowns: list[tuple[str, Any]] = []
        self._used = False

    @property
    def backend(self) -> AsyncBackend:
        """The async backend used by this manager."""
        return self._backend

    @property
    def was_used(self) -> bool:
        """Whether a shared async fixture was resolved for the current test."""
        return self._used

    @was_used.setter
    def was_used(self, value: bool) -> None:
        self._used = value

    @property
    def teardowns(self) -> tuple[tuple[str, Any], ...]:
        """Pending async teardowns (immutable view)."""
        return tuple(self._teardowns)

    @property
    def session(self) -> AsyncSession | None:
        """The underlying shared async session, or None if not yet created.

        Returns None while state is _Idle; returns the AsyncSession when _Live.
        Kept as ``AsyncSession | None`` at the property boundary — ADR-0007
        Rule 7b (find-lookup) accepts Optional here.
        """
        if isinstance(self._state, _Live):
            return self._state.session
        return None

    def resolve(self, func: Callable[..., Any], deps: dict[str, Any]) -> Any:
        """Run an async fixture, track teardowns, return the resolved value.

        Creates the shared session lazily on first call. Handles plain coroutines,
        async generators (with teardown tracking), and sync passthrough.

        Args:
            func: The fixture function to call.
            deps: Already-resolved dependency kwargs.

        Returns:
            The fixture value (awaited if async).

        Raises:
            FixtureSetupError: If the fixture raises during setup.

        """
        if isinstance(self._state, _Idle):
            # The manager holds this session across every test in the fixture
            # session. It calls ``backend.acquire_session()`` directly rather
            # than routing through ``acquire_session_guarded`` because the
            # guard's ``ContextVar`` would stay ``True`` for the manager's
            # entire lifetime (until ``cleanup()``), tripping middleware's
            # own guarded acquire for the next test that does not use shared
            # fixtures. The framework owns this seam; the guard protects
            # short-lived acquires that would nest in the same call stack.
            session = self._stack.enter_context(self._backend.acquire_session())
            self._state = _Live(session=session)

        self._used = True
        live_session = self._state.session

        try:
            result = func(**deps)
            if inspect.isasyncgen(result):
                value = live_session.run(anext(result))
                self._teardowns.append((getattr(func, "__name__", ""), result))
            elif inspect.iscoroutine(result):
                value = live_session.run(result)
            else:
                value = result
        except Exception as exc:
            name = getattr(func, "__name__", "")
            raise FixtureSetupError(name, exc) from exc

        return value

    def cleanup(self) -> None:
        """Drain async teardowns in LIFO order, then close the session stack.

        Closing the :class:`~contextlib.ExitStack` runs the session's
        ``__exit__`` (asyncgen shutdown, loop close) — session lifetime is
        owned by the stack.
        """
        if isinstance(self._state, _Idle):
            return
        live_session = self._state.session
        for name, gen in reversed(self._teardowns):

            def _drain(session: Any = live_session, generator: Any = gen) -> None:
                with contextlib.suppress(StopAsyncIteration):
                    session.run(anext(generator))

            safe_teardown(_drain, name, warn=_warn_teardown)
        self._stack.close()
        self._state = _Idle()
        self._teardowns.clear()
