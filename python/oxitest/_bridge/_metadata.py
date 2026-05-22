from __future__ import annotations

import weakref
from typing import Any, get_type_hints as _stdlib_hints

from oxitest._bridge._fn_metadata import get_metadata

__all__ = [
    "get_marks",
    "get_fixture_name",
    "get_type_hints_cached",
]

# Keyed by function identity; entries are GC'd when functions are unloaded.
_hints_cache: weakref.WeakKeyDictionary[Any, dict[str, Any]] = (
    weakref.WeakKeyDictionary()
)


def get_marks(obj: object) -> list[Any]:
    """Return the list of oxitest marks attached to obj, or [] if none."""
    return get_metadata(obj).marks


def get_fixture_name(fn: object, fallback: str = "") -> str:
    """Return the registered fixture name for fn, falling back to fallback."""
    name = get_metadata(fn).fixture_name
    return name if name else (getattr(fn, "__name__", None) or fallback)


def get_type_hints_cached(fn: Any) -> dict[str, Any]:
    """Return get_type_hints(fn, include_extras=True), cached by object identity.

    Safe to call on module-level functions and fixture functions that live for
    the duration of a test session. The WeakKeyDictionary releases entries when
    a function is garbage-collected.
    """
    try:
        cached = _hints_cache.get(fn)
        if cached is not None:
            return cached
        hints = _stdlib_hints(fn, include_extras=True)
        try:
            _hints_cache[fn] = hints
        except TypeError:
            pass  # fn is not weakly referenceable (e.g. C extension)
        return hints
    except Exception:
        # Fall back to uncached on any error (e.g. unresolvable forward refs).
        return _stdlib_hints(fn, include_extras=True)
