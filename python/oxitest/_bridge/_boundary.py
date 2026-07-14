"""Trust-boundary helpers.

Provides ``safe_call`` for catching arbitrary exceptions at boundaries
where user/plugin code is executed, plus focused wrappers for common
patterns (type-hint resolution, fixture teardown).
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


def safe_call(
    fn: Callable[[], _T],
    *,
    default: _T,
    on_error: Callable[[Exception], None] | None = None,
) -> _T:
    """Call *fn* and return *default* if it raises any ``Exception``.

    Use at trust boundaries where user/plugin code can raise anything.
    The optional *on_error* callback receives the caught exception for
    logging or warning before *default* is returned.
    """
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — boundary helper by design
        if on_error is not None:
            on_error(exc)
        return default


async def async_safe_call(
    coro: Coroutine[Any, Any, _T],
    *,
    default: _T,
    on_error: Callable[[Exception], None] | None = None,
) -> _T:
    """Await *coro* and return *default* if it raises any ``Exception``.

    Async counterpart of ``safe_call`` for trust boundaries that
    execute user coroutines or async generators.
    """
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — async boundary helper by design
        if on_error is not None:
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
    warn: Callable[[str, Exception], None] | None = None,
) -> None:
    """Call a teardown function, warning on failure instead of propagating.

    The *warn* callback receives ``(name, exc)`` on failure.  When omitted,
    failures are logged at debug level.
    """
    safe_call(
        fn,
        default=None,
        on_error=lambda exc: (
            warn(name, exc)
            if warn is not None
            else _trace("debug", __name__, f"Teardown {name!r} failed: {exc}")
        ),
    )
