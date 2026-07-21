"""Trust-boundary helpers.

Provides ``safe_call`` for catching arbitrary exceptions at boundaries
where user/plugin code is executed, plus focused wrappers for common
patterns (type-hint resolution, fixture teardown).

Optional-callback parameters follow ADR-0007 Rule 5: the default is a
module-level no-op function of the same signature. Consumers call the
callback unconditionally.
"""

from __future__ import annotations

__all__ = [
    "async_safe_call",
    "safe_call",
    "safe_teardown",
    "safe_type_hints",
]

from collections.abc import Callable, Coroutine
from typing import Any, TypeVar, get_type_hints as _stdlib_hints

from oxitest._oxitest import trace as _trace

_T = TypeVar("_T")


def _no_on_error(_exc: Exception) -> None:
    """No-op default for ``safe_call``/``async_safe_call`` ``on_error``."""


def _no_warn(_name: str, _exc: Exception) -> None:
    """No-op default for ``safe_teardown`` ``warn``."""


def safe_call(
    fn: Callable[[], _T],
    *,
    default: _T,
    on_error: Callable[[Exception], None] = _no_on_error,
) -> _T:
    """Call *fn* and return *default* if it raises any ``Exception``.

    Use at trust boundaries where user/plugin code can raise anything.
    The *on_error* callback receives the caught exception for logging or
    warning before *default* is returned. Defaults to a no-op that
    silently swallows the exception.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — boundary helper by design
        on_error(exc)
        return default


async def async_safe_call(
    coro: Coroutine[Any, Any, _T],
    *,
    default: _T,
    on_error: Callable[[Exception], None] = _no_on_error,
) -> _T:
    """Await *coro* and return *default* if it raises any ``Exception``.

    Async counterpart of ``safe_call`` for trust boundaries that
    execute user coroutines or async generators.
    """
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — async boundary helper by design
        on_error(exc)
        return default


def safe_type_hints(obj: Any, **kwargs: Any) -> dict[str, Any] | None:
    """Call ``get_type_hints(obj, **kwargs)`` and return ``None`` on failure.

    User code may have unresolvable forward references, missing imports,
    or broken annotations — all of which make ``get_type_hints`` raise.
    Callers should handle ``None`` with their own fallback.
    """
    return safe_call(
        lambda: _stdlib_hints(obj, **kwargs),
        default=None,
        on_error=lambda exc: _trace(
            "debug", __name__, f"Could not resolve type hints for {obj!r}: {exc}"
        ),
    )


def safe_teardown(
    fn: Callable[[], None],
    name: str = "",
    *,
    warn: Callable[[str, Exception], None] = _no_warn,
) -> None:
    """Call a teardown function, warning on failure instead of propagating.

    The *warn* callback receives ``(name, exc)`` on failure. Defaults to
    a no-op — callers that want teardown failures surfaced must pass an
    explicit ``warn`` callback (e.g. ``_warn_teardown`` from
    ``_fixture_context``).
    """
    safe_call(
        fn,
        default=None,
        on_error=lambda exc: warn(name, exc),
    )
