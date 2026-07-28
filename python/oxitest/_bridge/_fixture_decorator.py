from __future__ import annotations

__all__ = ["MARKER_ATTR", "_FixtureMarker", "fixture"]

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from oxitest._bridge._errors import UsageError
from oxitest._bridge._fixture_registry import LIFETIME_SCOPES
from oxitest._bridge._lifetime import Lifetime

_F = TypeVar("_F", bound=Callable[..., Any])

MARKER_ATTR = "__oxitest_fixture__"


@dataclass(frozen=True, slots=True)
class _FixtureMarker:
    """Attribute payload written by @oxi.fixture at import time."""

    lifetime: Lifetime


def fixture(*, lifetime: str) -> Callable[[_F], _F]:
    """Declare a fixture (ADR-0009 subset).

    ``"function"`` and ``"module"`` are accepted. The remaining tiers raise
    UsageError with pointers to their follow-on slices.

    Args:
        lifetime: Fixture lifetime tier. ``"function"`` builds a fresh
            instance per test; ``"module"`` builds one per test module and
            disposes it after that module's last test. ``"package"`` and
            ``"session"`` are reserved for follow-on slices.

    Returns:
        A decorator that attaches the fixture marker to the decorated
        callable and returns it unchanged.

    Raises:
        ValueError: If *lifetime* is not a recognised :class:`~oxitest.Lifetime`
            member.
        UsageError: If *lifetime* is valid but not yet implemented
            (``"package"`` or ``"session"``).

    Examples:
        Declare a function-scoped fixture in a ``__fixtures__.py`` module:

        >>> from oxitest import fixture
        >>> @fixture(lifetime="function")
        ... def db_conn() -> str:
        ...     return "connected"
        >>> db_conn()
        'connected'

        A module-scoped fixture is built once per test module. Use ``yield``
        to attach teardown, which runs after the module's last test:

        >>> @fixture(lifetime="module")
        ... def db_pool() -> str:
        ...     return "pool"
        >>> db_pool()
        'pool'

    """
    tier = Lifetime(lifetime)  # ValueError on unknown value — desired
    if tier not in LIFETIME_SCOPES:
        supported = ", ".join(repr(t.value) for t in LIFETIME_SCOPES)
        msg = (
            f"@oxi.fixture(lifetime={lifetime!r}) is not yet supported. "
            f"Supported so far: {supported}; see "
            f"kalonji-tools/oxitest#1710 (package), #1711 (session)."
        )
        raise UsageError(msg)

    marker = _FixtureMarker(lifetime=tier)

    def _apply(fn: _F) -> _F:
        setattr(fn, MARKER_ATTR, marker)
        return fn

    return _apply
