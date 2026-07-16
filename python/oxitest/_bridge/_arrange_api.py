"""@oxi.arrange decorator — declare side-effect-only fixture dependencies."""

from __future__ import annotations

__all__ = ["arrange"]

from collections.abc import Callable
from typing import TypeVar

from oxitest._bridge._fn_metadata import _update, get_metadata

_F = TypeVar("_F", bound=Callable[..., object])


def arrange(*args: type | str) -> Callable[[_F], _F]:
    """Declare fixtures to run around a test without binding their values.

    Args:
        *args: Injectable fixture classes (e.g. TempDir) and/or fixture names
            (strings). Order preserved.

    Returns:
        The decorated function, with metadata attached for the collector.

    Raises:
        TypeError: If called with no arguments (bare ``@oxi.arrange`` or
            ``@oxi.arrange()``), because there are no fixtures to arrange.
        TypeError: If an argument is not a type or a string — only
            ``@injectable`` classes and fixture name strings are valid.
        TypeError: If a type argument is not decorated with ``@injectable``
            (i.e. does not carry ``__oxitest_injectable__``).
        TypeError: If the same fixture appears more than once in a single call
            — the duplicate is almost certainly a copy-paste mistake.

    Note:
        Mark evaluation (skip, xfail, timeout) happens **before** the arrange
        phase, so arranged fixtures do NOT run for skipped or xfailed tests.
        This is intentional — arrange represents test-run setup, not overhead
        that should fire unconditionally.

    """
    # Bare form @oxi.arrange (no parens) passes the decorated function as the sole
    # argument — a callable but not a type.  Empty-args @oxi.arrange() is also
    # invalid.  Both share the same root cause (no fixtures named), so one guard
    # and one message covers both.
    if not args or (
        len(args) == 1 and callable(args[0]) and not isinstance(args[0], type)
    ):
        msg = (
            "@oxi.arrange requires at least one fixture — "
            "pass one or more @injectable classes or fixture name strings "
            "(and remember the parentheses: @oxi.arrange(TempDir) not @oxi.arrange)"
        )
        raise TypeError(msg)

    seen: set[type | str] = set()
    for arg in args:
        # Wrong kind: only types and strings are accepted.
        if not isinstance(arg, (type, str)):
            msg = (
                f"@oxi.arrange: expected an @injectable class or a fixture name "
                f"string, got {type(arg).__name__!r} ({arg!r})"
            )
            raise TypeError(msg)

        # Non-injectable type: @oxi.arrange resolves types via the fixture
        # registry at collection time; catching this at decoration gives
        # immediate feedback instead of a late FixtureTypeNotFoundError.
        if isinstance(arg, type) and not getattr(arg, "__oxitest_injectable__", False):
            msg = (
                f"@oxi.arrange: {arg.__name__} is not @injectable — "
                f"must be a BuiltinFixture (TempDir/StdCapture/Patcher/...), "
                f"a plugin-provided @injectable type, or a conftest fixture "
                f"with matching return annotation (passed via string name)"
            )
            raise TypeError(msg)

        # Duplicate: same type or name listed twice in one call is almost
        # always a copy-paste mistake; the fixture would run twice if we
        # allowed it, which is never the intent.
        if arg in seen:
            label = arg.__name__ if isinstance(arg, type) else repr(arg)
            msg = (
                f"duplicate fixture in @oxi.arrange: {label} appears more than once — "
                "remove the extra entry"
            )
            raise TypeError(msg)
        seen.add(arg)

    def decorator(fn: _F) -> _F:
        _update(fn, arranged=(*get_metadata(fn).arranged, *args))
        return fn

    return decorator
