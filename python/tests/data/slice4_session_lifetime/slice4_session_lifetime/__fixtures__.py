"""Session-lifetime fixture at the rootdir package (ADR-0009 slice 4).

``session`` means once per **task group** — one module unless a ``package``
declaration merges the subtree — not once per run and not once per worker
process, per ADR-0009 Rule 2 as corrected by Amendment 4. The acceptance test
reads this log back rather than parsing runner output, so it asserts on what
the fixture actually did.

Instance ids are PID-qualified because that is the whole question: under
parallel execution there must be exactly one instance per PID that ran a test,
and a run-scoped implementation would show one id across every PID.

``SLICE4_LOG`` is set by the calling test; a missing value is a hard error
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
    """Append one event line to the log named by ``SLICE4_LOG``."""
    path = os.environ["SLICE4_LOG"]
    with Path(f"{path}.{os.getpid()}").open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="process")
def engine() -> Iterator[str]:
    """One instance per task group."""
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
