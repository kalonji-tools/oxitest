"""Pluggable async backend for test execution.

Abstracts the async runtime so alternative backends (trio, uvloop) can be
swapped in via the plugin system.

The seam is scoped around an :class:`AsyncSession` context manager: backends
implement :func:`AsyncBackend.acquire_session` and the framework drives runtime
work through :meth:`AsyncSession.run`. Session lifetime is a caller decision —
short-lived usage inlines a ``with`` block; long-lived usage (e.g., shared
fixture managers) holds the session via :class:`contextlib.ExitStack`.

Session-scope asyncgen finalization runs on ``__exit__`` (previously incorrect:
``asyncio.run`` finalized on every call). Stray-task detection runs on every
``run`` call and applies to all sessions (previously shared-only).
"""

from __future__ import annotations

__all__ = [
    "_NULL_ASYNC_BACKEND",
    "AsyncBackend",
    "AsyncSession",
    "AsyncioBackend",
    "AsyncioSession",
    "resolve_backend",
]

import asyncio
import contextlib
from collections.abc import Coroutine, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, Never, Protocol, Self, TypeVar, runtime_checkable

from oxitest._bridge._diagnostic_collector import emit_diagnostic
from oxitest._bridge._errors import BackendNotFoundError, ConflictingBackendError
from oxitest._bridge._plugin_entry import ActivatedPluginEntry
from oxitest._bridge.result import DiagnosticSeverity

if TYPE_CHECKING:
    from oxitest._bridge.plugin_loader import PluginRegistry

_T = TypeVar("_T")


@runtime_checkable
class AsyncSession(Protocol):
    """A scoped async runtime session.

    Represents a single runtime context — one asyncio event loop, one trio
    nursery, etc. Multiple :meth:`run` calls on the same session share that
    context, so async generators produced by one call remain live for
    finalization on session exit.

    Sessions are context managers, and their lifetime is a caller decision.
    Short-lived usage inlines a ``with`` block. Long-lived usage — like a
    shared-scope fixture that spans many tests — holds the session via
    :class:`contextlib.ExitStack` and finalizes it when the enclosing scope
    tears down.

    See Also:
        - :meth:`AsyncBackend.acquire_session` — how sessions are produced.
        - :class:`AsyncioSession` — the default asyncio implementation.

    Examples:
        Any object with a matching ``run`` method satisfies the protocol:

        >>> from oxitest import AsyncSession
        >>> class MySession:
        ...     def run(self, coro, /):
        ...         return None
        >>> isinstance(MySession(), AsyncSession)
        True
        >>> isinstance(object(), AsyncSession)
        False

    """

    def run(self, coro: Coroutine[Any, Any, _T], /) -> _T:
        """Execute a coroutine to completion on this session."""
        ...


@runtime_checkable
class AsyncBackend(Protocol):
    """Pluggable async runtime backend.

    Backends produce :class:`AsyncSession` instances via
    :meth:`acquire_session`. The framework acquires a session per scope (one
    per test for function-scope work, one for the whole run for shared-scope
    work) and drives coroutines through :meth:`AsyncSession.run`.

    One session at a time by default. Set :attr:`supports_nested_acquire`
    to ``True`` on the concrete backend class to opt into overlapping
    sessions — the framework guard rejects nested acquires otherwise.

    See Also:
        - :attr:`oxitest.Plugin.async_backend` — how a plugin exposes a
          backend to oxitest.
        - :class:`AsyncioBackend` — the default implementation shipped
          with oxitest.

    Examples:
        Any object with a matching ``name`` property, ``acquire_session``
        method, and ``supports_nested_acquire`` attribute satisfies the
        protocol:

        >>> from oxitest import AsyncBackend
        >>> class MyBackend:
        ...     supports_nested_acquire = False
        ...     @property
        ...     def name(self) -> str: return "my"
        ...     def acquire_session(self): ...
        >>> isinstance(MyBackend(), AsyncBackend)
        True
        >>> isinstance(object(), AsyncBackend)
        False

    """

    @property
    def name(self) -> str:
        """Unique name for this backend (e.g. 'asyncio', 'trio')."""
        ...

    def acquire_session(self) -> AbstractContextManager[AsyncSession]:
        """Return a context manager that yields a fresh :class:`AsyncSession`.

        The session is scoped to the ``with`` block. Backends must finalize
        session-scope resources (event loops, nurseries, pending async
        generators) on context exit.
        """
        ...

    supports_nested_acquire: bool = False


class AsyncioSession:
    """asyncio implementation of :class:`AsyncSession`.

    Owns a single :class:`asyncio.AbstractEventLoop` for the session's
    lifetime. Multiple :meth:`run` calls share the loop, so async generators
    yielded from one ``run`` remain finalizable on ``__exit__`` — this is the
    whole reason for the session shape.

    On context exit, pending async generators are finalized via
    ``loop.shutdown_asyncgens()`` and the loop is closed.

    Stray tasks (created during a ``run`` but not awaited) are cancelled and
    surfaced as diagnostics on every ``run`` call.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        if not self._loop.is_closed():
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def run(self, coro: Coroutine[Any, Any, _T], /) -> _T:
        """Execute a coroutine on this session's event loop.

        After the coroutine completes, any tasks created during execution
        but not awaited (stray tasks) are cancelled and awaited to prevent
        resource leaks. A diagnostic is emitted for each such leak so
        developers know to await or cancel intentionally.

        Raises:
            RuntimeError: If the session has already exited.

        """
        if self._loop.is_closed():
            msg = "AsyncioSession has already exited"
            raise RuntimeError(msg)
        before = asyncio.all_tasks(self._loop)
        try:
            return self._loop.run_until_complete(coro)
        finally:
            after = asyncio.all_tasks(self._loop)
            stray = after - before
            if stray:
                for task in stray:
                    task.cancel()
                self._loop.run_until_complete(
                    asyncio.gather(*stray, return_exceptions=True)
                )
                emit_diagnostic(
                    DiagnosticSeverity.WARNING,
                    "async session",
                    f"leaked {len(stray)} task(s) (cancelled)",
                )


class AsyncioBackend:
    """Default async backend using :mod:`asyncio`."""

    supports_nested_acquire: bool = False

    @property
    def name(self) -> str:
        return "asyncio"

    @contextmanager
    def acquire_session(self) -> Iterator[AsyncSession]:
        session = AsyncioSession()
        with session:
            yield session


class _NullAsyncBackend:
    """Null-object stand-in when no plugin provides an async backend.

    Held on ``Plugin.async_backend`` and ``PluginRegistry`` fields as the
    canonical "no backend registered" value. Discovery in
    :func:`resolve_backend` filters this out by identity (``is not
    _NULL_ASYNC_BACKEND``); any actual method call is a bug in the caller's
    filter and raises ``AssertionError``.
    """

    supports_nested_acquire: bool = False  # required: runtime_checkable Protocol

    @property
    def name(self) -> str:
        return "null"

    def acquire_session(self) -> Never:
        msg = "null-object AsyncBackend method called — discovery filter is broken"
        raise AssertionError(msg)


_NULL_ASYNC_BACKEND = _NullAsyncBackend()


def resolve_backend(name: str, registry: PluginRegistry) -> AsyncBackend:
    """Resolve the active backend by config name.

    Raises:
        BackendNotFoundError: if no backend matches the config name.
        ConflictingBackendError: if multiple plugins register the same name.

    """
    plugin_backends = [
        (entry.module_name, entry.plugin.async_backend)
        for entry in registry.entries
        if isinstance(entry, ActivatedPluginEntry)
        and entry.plugin.async_backend is not _NULL_ASYNC_BACKEND
    ]

    if name == "asyncio":
        conflicts = [m for m, b in plugin_backends if b.name == "asyncio"]
        if conflicts:
            msg = "asyncio"
            raise ConflictingBackendError(msg, conflicts)
        return AsyncioBackend()

    candidates = [(m, b) for m, b in plugin_backends if b.name == name]
    if not candidates:
        raise BackendNotFoundError(name)
    if len(candidates) > 1:
        raise ConflictingBackendError(name, [m for m, _ in candidates])
    return candidates[0][1]
