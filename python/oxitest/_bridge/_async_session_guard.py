"""Framework-internal guard around :func:`AsyncBackend.acquire_session`.

Nested :func:`AsyncBackend.acquire_session` calls are an antipattern in every
runtime the framework knows about:

- **trio** forbids nested :func:`trio.run` by design (single-runtime nurseries).
- **asyncio** raises on the common nested case (:func:`asyncio.run` while
  another loop is running).
- Cross-loop bugs (a resource bound to loop A used from loop B) manifest as
  runtime errors far from the acquire site.

Backends are the source of truth on whether their runtime tolerates nested
acquires, so :class:`AsyncBackend` carries a :attr:`supports_nested_acquire`
field. The framework defaults to strict: nested :func:`acquire_session_guarded`
raises :class:`RuntimeError` naming the backend and pointing at the correct
pattern (pass the existing session explicitly).

This helper is framework-internal — external plugin authors calling
:func:`AsyncBackend.acquire_session` directly bypass the guard by design. The
framework owns the seams it owns, and this guard covers those.
"""

from __future__ import annotations

__all__ = ["acquire_session_guarded"]

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oxitest._bridge._async_backend import AsyncBackend, AsyncSession

_SESSION_OPEN: ContextVar[bool] = ContextVar("_SESSION_OPEN", default=False)


@contextmanager
def acquire_session_guarded(backend: AsyncBackend) -> Iterator[AsyncSession]:
    """Acquire a session from *backend* with framework-level nesting protection.

    If ``backend.supports_nested_acquire`` is ``False`` (the default) and a
    session is already open in the current context, raises
    :class:`RuntimeError` naming the backend. Backend authors whose runtime is
    genuinely nesting-safe should set ``supports_nested_acquire = True`` on the
    concrete class.

    Raises:
        RuntimeError: When nesting is attempted on a backend that does not
            support it. Message includes the backend's ``name`` for
            actionable diagnosis.

    """
    if not backend.supports_nested_acquire and _SESSION_OPEN.get():
        msg = (
            f"{backend.name} does not support nested acquire_session(). "
            "Pass the existing session explicitly instead of opening a new one."
        )
        raise RuntimeError(msg)
    token = _SESSION_OPEN.set(True)
    try:
        with backend.acquire_session() as session:
            yield session
    finally:
        _SESSION_OPEN.reset(token)
