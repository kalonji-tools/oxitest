"""Fixtures anchored at ``api/``, reachable from ``api/`` and below.

``tx`` is deliberately redeclared in ``api/v1/__fixtures__.py``. Both are
visible from ``v1``, so the shortcut has to pick one, and "nearest ancestor
wins" is only observable when the two carry different labels.

``api`` collides with this package's own segment name on purpose — it is what
makes ADR-0009 Rule 5's naming-clash rule testable. The rule was vacuous until
shortcut access existed, because ``FixturesProxy.__getattr__`` had no fixture
branch for the segment to win against.
"""

from __future__ import annotations

import oxitest as oxi

from slice7_shortcut._kinds import Shadowed, Tx


@oxi.fixture(lifetime="function")
def tx() -> Tx:
    return Tx("api")


@oxi.fixture(lifetime="function")
def api() -> Shadowed:
    return Shadowed("shadowed-by-the-segment")


@oxi.fixture(lifetime="function")
async def async_tx() -> Tx:
    return Tx("async-api")
