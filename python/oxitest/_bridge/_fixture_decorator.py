from __future__ import annotations

__all__ = ["MARKER_ATTR", "_FixtureMarker", "fixture"]

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from oxitest._bridge._errors import InternalError
from oxitest._bridge._fixture_registry import LIFETIME_SCOPES
from oxitest._bridge._lifetime import Lifetime

_F = TypeVar("_F", bound=Callable[..., Any])

MARKER_ATTR = "__oxitest_fixture__"


@dataclass(frozen=True, slots=True)
class _FixtureMarker:
    """Attribute payload written by @oxi.fixture at import time."""

    lifetime: Lifetime
    autouse: bool = False


def fixture(*, lifetime: str, autouse: bool = False) -> Callable[[_F], _F]:
    """Declare a fixture (ADR-0009 Rule 2).

    All four lifetime tiers are accepted.

    Args:
        lifetime: Fixture lifetime tier. ``"function"`` builds a fresh instance
            per test; ``"module"`` builds one per test module and disposes it
            after that module's last test; ``"package"`` builds exactly one per
            run for the declaring directory's subtree, which co-locates that
            subtree onto a single worker and so costs the run parallelism;
            ``"process"`` builds one per **worker process**, so its instance
            count is whatever ``-n`` is — the only tier the user sets with a
            flag rather than with directory layout, and therefore not a
            run-wide singleton. It is legal only in a rootdir package. Work that
            must happen exactly once per run belongs at rootdir ``"package"``.
        autouse: When ``True``, the fixture runs for every test in its B1
            boundary without being requested, for its side effects — the value
            is discarded unless the test also requests it, in which case both
            routes share one instance rather than building twice.

            How often it runs follows *lifetime*, and that is a **rate rather
            than a boundary event**: the build happens inside the first test
            that reaches the boundary, so a setup failure is reported against
            that test and a boundary whose tests are all skipped never fires at
            all. Where several autouse fixtures apply to one test they run
            widest-lifetime-first.

            To opt a subtree out, declare a fixture of the same name without
            ``autouse`` at a deeper anchor; the suppression is boundary-local
            and the registration notice reports it.

            ``autouse=True`` with ``lifetime="function"`` on an ``async``
            factory is refused at registration: it would fire for the sync
            tests in its boundary too, manufacturing the ADR-0006 illegal cell
            for tests that never asked for it.

    Returns:
        A decorator that attaches the fixture marker to the decorated
        callable and returns it unchanged.

    Raises:
        ValueError: If *lifetime* is not a recognised :class:`~oxitest.Lifetime`
            member.
        InternalError: If *lifetime* is a recognised member with no scope mapping.

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

        An autouse fixture runs without any test naming it. The decorator stays
        a pure marker, so the function itself is unchanged:

        >>> @fixture(lifetime="module", autouse=True)
        ... def migrations() -> str:
        ...     return "applied"
        >>> migrations()
        'applied'

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
        raise InternalError(msg)

    marker = _FixtureMarker(lifetime=tier, autouse=autouse)

    def _apply(fn: _F) -> _F:
        setattr(fn, MARKER_ATTR, marker)
        return fn

    return _apply
