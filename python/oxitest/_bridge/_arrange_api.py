"""@oxi.arrange decorator — declare side-effect-only fixture dependencies."""

from __future__ import annotations

__all__ = ["arrange"]

from collections.abc import Callable
from typing import TypeVar

from oxitest._bridge._fn_metadata import _update, get_or_create

_F = TypeVar("_F", bound=Callable[..., object])


def arrange(*args: type[object] | str) -> Callable[[_F], _F]:
    """Declare fixtures to run around a test without binding their values.

    Args:
        *args: Injectable fixture classes (e.g. TempDir) and/or fixture names
            (strings). Order preserved.

    Returns:
        The decorated function, with metadata attached for the collector.

    """

    def decorator(fn: _F) -> _F:
        meta = get_or_create(fn)
        _update(fn, arranged=(*meta.arranged, *args))
        return fn

    return decorator
