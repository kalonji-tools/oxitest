"""@oxi.arrange decorator — declare side-effect-only fixture dependencies."""

from __future__ import annotations

__all__ = ["arrange"]

from collections.abc import Callable
from typing import TypeVar

from oxitest._bridge._fn_metadata import _update, get_or_create

_F = TypeVar("_F", bound=Callable[..., object])


def arrange(*args: type | str) -> Callable[[_F], _F]:
    """Declare fixtures to run around a test without binding their values.

    Args:
        *args: Injectable fixture classes (e.g. TempDir) and/or fixture names
            (strings). Order preserved.

    Returns:
        The decorated function, with metadata attached for the collector.

    Raises:
        TypeError: If a type argument is not decorated with ``@injectable``
            (i.e. does not carry ``__oxitest_injectable__``).

    """
    for arg in args:
        if isinstance(arg, type) and not getattr(arg, "__oxitest_injectable__", False):
            msg = (
                f"@oxi.arrange: {arg.__name__} is not @injectable — "
                f"must be a BuiltinFixture (TempDir/StdCapture/Patcher/...), "
                f"a plugin-provided @injectable type, or a conftest fixture "
                f"with matching return annotation (passed via string name)"
            )
            raise TypeError(msg)

    def decorator(fn: _F) -> _F:
        meta = get_or_create(fn)
        _update(fn, arranged=(*meta.arranged, *args))
        return fn

    return decorator
