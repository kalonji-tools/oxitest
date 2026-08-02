"""A ``shared=True`` fixture, present only to trigger auto-arrangement.

Modules using a shared fixture are pinned to the coordinator by the arrange
stage. That pin is the point: it gives the coordinator an arranged phase in
addition to the inprocess one, and two coordinator phases are what a per-phase
process drain would rebuild the fixture between.
"""

from __future__ import annotations

from oxitest import Fixtures

fx = Fixtures()


@fx.fixture(shared=True)
def pinned() -> str:
    return "pinned"
