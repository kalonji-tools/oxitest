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
    "_check_async_dep",
    "_reject_async_in_sync",
    "_reject_nonshared_async",
]

import contextlib
import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import (
        AsyncBackend,
        SharedAsyncSession,
    )
from oxitest._bridge._boundary import safe_teardown
from oxitest._bridge._errors import FixtureSetupError
from oxitest._bridge._fixture_context import _warn_teardown

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
        f"non-shared async fixture '{dep_name}' \u2014 "
        f"lifetime mismatch",
    )


AsyncPolicy = Callable[[str, Any, str], None]


# ── SharedAsyncManager ────────────────────────────────────────────────────────


class SharedAsyncManager:
    """Manages shared async fixture lifecycle: session creation, resolution, teardown.

    Extracted from FixtureSession to isolate the async fixture management concern.
    The manager lazily creates a SharedAsyncSession on the first resolve() call,
    tracks async generator teardowns, and drains them in LIFO order on cleanup().
    """

    def __init__(self, async_backend: AsyncBackend) -> None:
        self._backend = async_backend
        self._session: SharedAsyncSession | None = None
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
    def session(self) -> SharedAsyncSession | None:
        """The underlying shared async session, or None if not yet created."""
        return self._session

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
        if self._session is None:
            self._session = self._backend.create_shared_session()

        self._used = True

        try:
            result = func(**deps)
            if inspect.isasyncgen(result):
                value = self._session.run(anext(result))
                self._teardowns.append((getattr(func, "__name__", ""), result))
            elif inspect.iscoroutine(result):
                value = self._session.run(result)
            else:
                value = result
        except Exception as exc:
            name = getattr(func, "__name__", "")
            raise FixtureSetupError(name, exc) from exc

        return value

    def cleanup(self) -> None:
        """Drain async teardowns in LIFO order, then close the session."""
        if self._session is None:
            return
        for name, gen in reversed(self._teardowns):

            def _drain(session: Any = self._session, generator: Any = gen) -> None:
                with contextlib.suppress(StopAsyncIteration):
                    session.run(anext(generator))

            safe_teardown(_drain, name, warn=_warn_teardown)
        self._session.close()
        self._session = None
        self._teardowns.clear()
