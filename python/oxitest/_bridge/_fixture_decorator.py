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
    """Declare a fixture (ADR-0009 Rule 2).

    All four lifetime tiers are accepted.

    Args:
        lifetime: Fixture lifetime tier. ``"function"`` builds a fresh instance
            per test; ``"module"`` builds one per test module and disposes it
            after that module's last test; ``"package"`` builds exactly one per
            run for the declaring directory's subtree, which co-locates that
            subtree onto a single worker and so costs the run parallelism;
            ``"session"`` builds one per **worker task group** — which is a
            single module unless a ``"package"`` declaration merges the subtree,
            so it is not a true singleton and is not even reliably wider than
            ``"module"``. It is legal only in a rootdir package. Work that must
            happen exactly once per run belongs at rootdir ``"package"``.

    Returns:
        A decorator that attaches the fixture marker to the decorated
        callable and returns it unchanged.

    Raises:
        ValueError: If *lifetime* is not a recognised :class:`~oxitest.Lifetime`
            member.
        UsageError: If *lifetime* is a recognised member with no scope mapping.

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
        # Unreachable for every current member: LIFETIME_SCOPES is total over
        # Lifetime as of #1711. Kept so a member added without a scope mapping
        # fails here, at the declaration, rather than as a KeyError deeper in
        # the registrar where the offending decorator is no longer in view.
        supported = ", ".join(repr(t.value) for t in LIFETIME_SCOPES)
        msg = (
            f"@oxi.fixture(lifetime={lifetime!r}) has no scope mapping. "
            f"Supported: {supported}. This is an oxitest bug — please report it "
            f"at kalonji-tools/oxitest."
        )
        raise UsageError(msg)

    marker = _FixtureMarker(lifetime=tier)

    def _apply(fn: _F) -> _F:
        setattr(fn, MARKER_ATTR, marker)
        return fn

    return _apply
