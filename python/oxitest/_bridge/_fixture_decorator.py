from __future__ import annotations

__all__ = ["MARKER_ATTR", "_FixtureMarker", "fixture"]

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from oxitest._bridge._errors import UsageError
from oxitest._bridge._lifetime import Lifetime

_F = TypeVar("_F", bound=Callable[..., Any])

MARKER_ATTR = "__oxitest_fixture__"


@dataclass(frozen=True, slots=True)
class _FixtureMarker:
    """Attribute payload written by @oxi.fixture at import time."""

    lifetime: Lifetime


def fixture(*, lifetime: str) -> Callable[[_F], _F]:
    """Declare a fixture (ADR-0009 slice 1 subset).

    Only lifetime="function" is accepted in slice 1. Other tiers raise
    UsageError with pointers to their follow-on slices.

    Args:
        lifetime: Fixture lifetime tier. Only ``"function"`` is supported
            in slice 1; ``"module"``, ``"package"``, and ``"session"``
            are reserved for follow-on slices.

    Returns:
        A decorator that attaches the fixture marker to the decorated
        callable and returns it unchanged.

    Raises:
        ValueError: If *lifetime* is not a recognised :class:`~oxitest.Lifetime`
            member.
        UsageError: If *lifetime* is valid but not yet implemented
            (i.e. anything other than ``"function"`` in slice 1).

    Examples:
        Declare a function-scoped fixture in a ``__fixtures__.py`` module:

        >>> from oxitest import fixture
        >>> @fixture(lifetime="function")
        ... def db_conn() -> str:
        ...     return "connected"
        >>> db_conn()
        'connected'

    """
    tier = Lifetime(lifetime)  # ValueError on unknown value — desired
    if tier is not Lifetime.FUNCTION:
        msg = (
            f"@oxi.fixture(lifetime={lifetime!r}) is not yet supported. "
            f"Slice 1 ships only 'function'; see kalonji-tools/oxitest#1709 "
            f"(module), #1710 (package), #1711 (session)."
        )
        raise UsageError(msg)

    marker = _FixtureMarker(lifetime=tier)

    def _apply(fn: _F) -> _F:
        setattr(fn, MARKER_ATTR, marker)
        return fn

    return _apply
