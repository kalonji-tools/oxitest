from __future__ import annotations

import contextlib
import weakref
from typing import Any, get_type_hints as _stdlib_hints

from oxitest._bridge._fn_metadata import get_metadata

__all__ = [
    "get_fixture_name",
    "get_marks",
    "get_type_hints_cached",
]

# Keyed by function identity; entries are GC'd when functions are unloaded.
_hints_cache: weakref.WeakKeyDictionary[Any, dict[str, Any]] = (
    weakref.WeakKeyDictionary()
)


def get_marks(obj: object) -> tuple[Any, ...]:
    """Return the oxitest marks attached to obj as an immutable tuple."""
    return tuple(get_metadata(obj).marks)


def get_fixture_name(fn: object, fallback: str = "") -> str:
    """Return the registered fixture name for fn, falling back to fallback."""
    name = get_metadata(fn).fixture_name
    return name or (getattr(fn, "__name__", None) or fallback)


def get_type_hints_cached(fn: Any) -> dict[str, Any]:
    """Return get_type_hints(fn, include_extras=True), cached by object identity.

    Safe to call on module-level functions and fixture functions that live for
    the duration of a test session. The WeakKeyDictionary releases entries when
    a function is garbage-collected.
    """
    with contextlib.suppress(TypeError):
        cached = _hints_cache.get(fn)
        if cached is not None:
            return cached

    try:
        hints = _stdlib_hints(fn, include_extras=True)
    except (NameError, AttributeError, TypeError):
        # Unresolvable forward refs, missing attributes, or unhashable fn.
        return {}

    with contextlib.suppress(TypeError):
        # fn may not be weakly referenceable (e.g. C extension)
        _hints_cache[fn] = hints
    return hints
