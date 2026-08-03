"""A ``shared=True`` fixture — the legacy wide tier, which stays task-scoped.

Decision 8 leaves ``shared=True`` alone: it drains at ``end_task``, so a worker
that handles two task groups builds it twice. This project exists to pin that
it was *not* dragged along when the user tier moved to the process boundary.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures

fx = Fixtures()


def _record(event: str) -> None:
    with Path(os.environ["NEGATIVES_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@fx.fixture(shared=True)
def legacy_shared() -> str:
    _record(f"SHARED_SETUP {os.getpid()}")
    return "shared"
