"""A ``shared=True`` fixture, present to trigger auto-arrangement — and logging.

Modules using a shared fixture are pinned to the coordinator by the arrange
stage. That pin is the point: it gives the coordinator an arranged phase in
addition to the inprocess one, and two coordinator phases are what a per-phase
process drain would rebuild the fixture between.

It logs its own setup and teardown because those two phases also make this the
only project that can observe a *task*-scoped tier across a coordinator phase
boundary — see the cross-phase assertion in the acceptance test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from oxitest import Fixtures

fx = Fixtures()


def _record(event: str) -> None:
    with Path(os.environ["PROC_COORD_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@fx.fixture(shared=True)
def pinned() -> Iterator[str]:
    _record(f"SHARED_SETUP {os.getpid()}")
    yield "pinned"
    _record(f"SHARED_TEARDOWN {os.getpid()}")
