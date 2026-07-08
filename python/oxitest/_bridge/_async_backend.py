"""Pluggable async backend for test execution.

Abstracts the async runtime so alternative backends (trio, uvloop) can be
swapped in via the plugin system.
"""

from __future__ import annotations

__all__ = [
    "AsyncBackend",
    "AsyncioBackend",
    "AsyncioSharedSession",
    "SharedAsyncSession",
    "resolve_backend",
]

import asyncio
import contextlib
import warnings
from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

from oxitest._bridge._errors import BackendNotFoundError, ConflictingBackendError

if TYPE_CHECKING:
    from oxitest._bridge.plugin_loader import PluginRegistry

_T = TypeVar("_T")


@runtime_checkable
class SharedAsyncSession(Protocol):
    """A long-lived async session for shared fixture resolution."""

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Execute a coroutine on the persistent session."""
        ...

    def close(self) -> None:
        """Tear down the session and release resources."""
        ...


@runtime_checkable
class AsyncBackend(Protocol):
    """Pluggable async runtime backend."""

    @property
    def name(self) -> str:
        """Unique name for this backend (e.g. 'asyncio', 'trio')."""
        ...

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Run a coroutine to completion (per-test, fresh context)."""
        ...

    def create_shared_session(self) -> SharedAsyncSession:
        """Create a long-lived session for shared fixture resolution."""
        ...


class AsyncioSharedSession:
    """Wraps an asyncio event loop as a SharedAsyncSession."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = asyncio.new_event_loop()

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Execute a coroutine on the persistent event loop and return its result.

        After the coroutine completes, any tasks that were created during
        execution but not awaited (stray tasks) are cancelled and awaited to
        prevent resource leaks.  A warning is emitted for each cancelled task.

        Raises:
            RuntimeError: If the session has already been closed.

        """
        if self._loop is None or self._loop.is_closed():
            msg = "SharedAsyncSession is closed"
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
                warnings.warn(
                    f"leaked {len(stray)} task(s) (cancelled)",
                    stacklevel=2,
                )

    def close(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            with contextlib.suppress(Exception):
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()
        self._loop = None


class AsyncioBackend:
    """Default async backend using `asyncio`."""

    @property
    def name(self) -> str:
        return "asyncio"

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        return asyncio.run(coro)

    def create_shared_session(self) -> SharedAsyncSession:
        return AsyncioSharedSession()


def resolve_backend(name: str, registry: PluginRegistry) -> AsyncBackend:
    """Resolve the active backend by config name.

    Raises:
        BackendNotFoundError: if no backend matches the config name.
        ConflictingBackendError: if multiple plugins register the same name.

    """
    plugin_backends = [
        (entry.module_name, entry.plugin.async_backend)
        for entry in registry.entries
        if entry.plugin is not None and entry.plugin.async_backend is not None
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
