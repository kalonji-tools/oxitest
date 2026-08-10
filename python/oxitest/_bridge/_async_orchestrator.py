"""Async fixture orchestration — single source of truth for async fixture lifecycle.

Consolidates SharedAsyncManager (session creation, resolution, teardown) and
async dependency policy functions (_check_async_dep, _reject_async_in_sync,
_reject_nonshared_async) that were previously spread across _fixture_session.py
and _fixture_instantiator.py.
"""

from __future__ import annotations

__all__ = [
    "PROCESS_BOUNDARY",
    "SESSION_BOUNDARY",
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
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import (
        AsyncBackend,
        AsyncSession,
    )
from oxitest._bridge._boundary import (
    advance_async_gen,
    safe_teardown,
    setup_completed,
)
from oxitest._bridge._errors import AsyncDependencyError, FixtureSetupError
from oxitest._bridge._fixture_context import _warn_teardown

#: Teardown-store key for fixtures disposed at the end of the run. Every other
#: key is the boundary's own identity — a module path, today. Not a valid
#: module path itself, so it cannot collide with one.
SESSION_BOUNDARY = "<session>"
#: Teardowns that live as long as the *process* — ``lifetime="process"``
#: fixtures, once #1777 made that tier genuinely per-process. Distinct from
#: :data:`SESSION_BOUNDARY`, which now holds only the task-lifetime wide
#: teardowns (``shared=True``): the two drain at different boundaries, and
#: sharing one key is what let an async process-lifetime fixture be disposed at
#: the end of its first task group and then handed to every later test.
PROCESS_BOUNDARY = "<process>"

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
        # AsyncDependencyError rather than its FixtureSetupError parent: this
        # refusal is a wiring mistake and votes for exit 4, while the parent
        # also wraps genuine exceptions from a user's fixture body, which are
        # ordinary failures (#1761). All three call sites below raise through
        # here, so one class covers every lifetime refusal.
        raise AsyncDependencyError(fixture_name, RuntimeError(msg))


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
        #: Pending async-generator teardowns, keyed by the boundary whose exit
        #: disposes them. ``SESSION_BOUNDARY`` holds the ones that live for the
        #: whole run. One store rather than two: a separate session-level list
        #: meant two drain paths, and the two drifted into different guard
        #: orderings — one of which discarded teardowns.
        self._teardowns: dict[str, list[tuple[str, Any]]] = {}
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
        """Every pending async teardown, across all boundaries (immutable)."""
        return tuple(pair for pairs in self._teardowns.values() for pair in pairs)

    @staticmethod
    def _drain_pairs(pairs: list[tuple[str, Any]], live_session: Any) -> None:
        """Advance each generator past its ``yield``, most recent first.

        The single place a pending async generator is resumed. *live_session*
        is passed rather than read from ``self._state`` so the caller has to
        have proved a session exists — resuming on a closed or absent loop is
        the failure this whole mechanism exists to prevent.
        """
        for name, gen in reversed(pairs):

            def _drain(session: Any = live_session, generator: Any = gen) -> None:
                # Registration precedes the advance (#1962), so a pair here may
                # name a generator whose setup never completed. Resuming an
                # unstarted one would run its setup during teardown.
                if not setup_completed(generator):
                    return
                with contextlib.suppress(StopAsyncIteration):
                    session.run(anext(generator))

            safe_teardown(_drain, name, warn=_warn_teardown)

    def ensure_session(self) -> Any:
        """Acquire the shared session if idle, and return it.

        The one place this manager's session is created. Two callers:
        :meth:`resolve`, which needs a loop to run a fixture on, and the
        promotion path in the executor, which needs the loop to exist
        *before* the test body starts — the lazy ``fx.`` path never calls
        :meth:`resolve`, so without that the manager would stay ``_Idle``,
        ``session`` would be ``None``, and ``cleanup`` would return before
        draining anything.

        Calls ``backend.acquire_session()`` directly rather than routing
        through ``acquire_session_guarded``: the guard's ``ContextVar`` would
        stay ``True`` for this manager's whole lifetime (until
        :meth:`cleanup`), tripping the middleware's own guarded acquire for
        the next test that uses no shared fixtures. The framework owns this
        seam; the guard protects short-lived acquires that would nest in the
        same call stack.
        """
        if isinstance(self._state, _Idle):
            session = self._stack.enter_context(self._backend.acquire_session())
            self._state = _Live(session=session)
        return self._state.session

    def drain_boundary(self, boundary: str) -> None:
        """Run the pending teardowns registered against *boundary*.

        Called when the boundary's scope exits — a module's last test, say —
        so disposal happens where the fixture's declared lifetime says it
        should, rather than being deferred to the end of the run. Ten
        surveyed frameworks agree on that, and every deferral any of them
        shipped was later logged as a bug (see #1739).
        """
        # Guard before popping. Popping first would discard the teardowns on
        # the very path that cannot run them, turning "no loop to drain on"
        # into "silently never disposed".
        if isinstance(self._state, _Idle) or boundary not in self._teardowns:
            return
        self._drain_pairs(self._teardowns.pop(boundary), self._state.session)

    def register_teardown(
        self, name: str, agen: Any, *, boundary: str | None = None
    ) -> None:
        """Track an async generator whose post-``yield`` half is still pending.

        Used by the lazy ``fx.`` resolution path, which advances the generator
        on the *test's* loop rather than through :meth:`resolve`, but still
        needs its drain to happen somewhere the loop is guaranteed alive.

        *boundary* names the scope whose exit should dispose it — a module
        path for module lifetime. ``None`` means session end, which is both
        the correct answer for session-lifetime fixtures and the fallback for
        anything whose boundary cannot be determined.
        """
        key = SESSION_BOUNDARY if boundary is None else boundary
        self._teardowns.setdefault(key, []).append((name, agen))

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

    def resolve(
        self,
        func: Callable[..., Any],
        deps: dict[str, Any],
        *,
        boundary: str | None = None,
    ) -> Any:
        """Run an async fixture, track teardowns, return the resolved value.

        Creates the shared session lazily on first call. Handles plain coroutines,
        async generators (with teardown tracking), and sync passthrough.

        Args:
            func: The fixture function to call.
            deps: Already-resolved dependency kwargs.
            boundary: Scope whose exit disposes the teardown, forwarded to
                :meth:`register_teardown`. ``PROCESS_BOUNDARY`` for the process
                tier; ``None`` — task-lifetime — for everything else. Omitting
                it sent every wide async fixture to ``SESSION_BOUNDARY``, so a
                process-lifetime one was disposed at the end of its first task
                group and reused by every test after it (#1777).

        Returns:
            The fixture value (awaited if async).

        Raises:
            FixtureSetupError: If the fixture raises during setup.

        """
        live_session = self.ensure_session()
        self._used = True

        try:
            result = func(**deps)
            if inspect.isasyncgen(result):
                # Registered before the advance (#1962): the advance runs the
                # body on the loop, and an interrupt landing there would leave
                # a set-up fixture with nothing registered to dispose it.
                self.register_teardown(
                    getattr(func, "__name__", ""), result, boundary=boundary
                )
                value = live_session.run(advance_async_gen(result))
            elif inspect.iscoroutine(result):
                value = live_session.run(result)
            else:
                value = result
        except Exception as exc:
            name = getattr(func, "__name__", "")
            raise FixtureSetupError(name, exc) from exc

        return value

    def drain_task(self) -> None:
        """Drain every teardown whose lifetime ends with this task group (#1777).

        Everything except :data:`PROCESS_BOUNDARY`, and the session stack stays
        **open**. Closing it runs the session's ``__exit__`` — asyncgen
        shutdown and loop close — and a process-lifetime async fixture still
        needs that loop alive to be resumed at process end. :meth:`cleanup`
        owns the close.
        """
        if isinstance(self._state, _Idle):
            return
        # Clamp: effective boundary is min(declared boundary, loop lifetime).
        # A boundary whose scope never exited would otherwise have its
        # teardown finalised by loop close instead of run — the failure mode
        # pytest-asyncio fixed in 0.25.3 and anyio in 4.1.0 (see #1739).
        # Session-lifetime teardowns drain last of this set, so a narrower
        # fixture can still touch a wider one on its way out.
        live_session = self._state.session
        narrower = [
            b for b in self._teardowns if b not in (SESSION_BOUNDARY, PROCESS_BOUNDARY)
        ]
        # Deepest path first, so this set honours narrower-before-wider the way
        # the sentinels above already do. The keys are real paths — a module
        # path or a package anchor — so depth *is* nesting. Insertion order got
        # this right only by accident: a package fixture built before a module
        # one inside it registered first and so drained first, disposing the
        # value the inner teardown was about to touch. Only reachable on the
        # worker path, where nothing calls `end_package` and this is the drain
        # (#1839).
        narrower.sort(key=lambda path: len(PurePath(path).parts), reverse=True)
        for boundary in narrower:
            self._drain_pairs(self._teardowns.pop(boundary), live_session)
        self._drain_pairs(self._teardowns.pop(SESSION_BOUNDARY, []), live_session)

    def cleanup(self) -> None:
        """Drain what is left, widest last, then close the session stack.

        Called once per process. Closing the :class:`~contextlib.ExitStack`
        runs the session's ``__exit__`` (asyncgen shutdown, loop close) —
        session lifetime is owned by the stack.

        Process-lifetime teardowns drain after everything else for the same
        reason session-lifetime ones used to: the widest value must still be
        reachable while narrower ones are being disposed.
        """
        if isinstance(self._state, _Idle):
            return
        self.drain_task()
        # Still `_Live`: drain_task deliberately leaves the stack open.
        live_session = self._state.session
        self._drain_pairs(self._teardowns.pop(PROCESS_BOUNDARY, []), live_session)
        self._stack.close()
        self._state = _Idle()
        self._teardowns.clear()
