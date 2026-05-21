"""Pluggable async backend for test execution.

Abstracts the async runtime so alternative backends (trio, uvloop) can be
swapped in via the plugin system in a future phase.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, Protocol, TypeVar

_T = TypeVar("_T")


class AsyncBackend(Protocol):
    """Protocol for async test execution backends."""

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Run a coroutine synchronously and return its result."""
        ...


class AsyncioBackend:
    """Default async backend using ``asyncio.run()``."""

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        return asyncio.run(coro)


_default_backend: AsyncBackend = AsyncioBackend()


def get_async_backend() -> AsyncBackend:
    """Return the active async backend (currently always AsyncioBackend)."""
    return _default_backend
