"""An autouse process-lifetime fixture at the rootdir (ADR-0009 slice 9, #1716).

``process`` fires once per process that resolves it — ``<= 1 + N`` for N
workers plus the coordinator. That is a *range*, because a worker that never
receives a task reaching the boundary never fires the fixture, and which worker
receives which task group is a scheduling outcome.

So the acceptance test does not assert the range. It derives the expected count
from what actually ran: one SETUP per distinct PID that ran a test. Asserting
``1 <= n <= 1 + N`` would pass even if the fixture fired once per *test*, which
is the regression the test exists to catch.

Instance ids are PID-qualified so a rebuild inside one process is
distinguishable from a build in another — the two failures look identical in a
bare count. ``setup_pids`` deliberately keeps duplicates for this reason.

Nothing here is ever requested by a test. That is the point: the fixture is
autouse, so if it does not fire, no test fails and no error is raised — the
log is the only witness.

``SLICE9_PROC_LOG`` is set by the calling test; a missing value is a hard error
because every assertion downstream would otherwise pass vacuously.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    """Append one event line to the log named by ``SLICE9_PROC_LOG``."""
    with Path(f"{os.environ['SLICE9_PROC_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="process", autouse=True)
def boot() -> Iterator[str]:
    """One instance per process that resolves it — fired without any request."""
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
