"""Rootdir process-lifetime fixture, logging every build and disposal.

The acceptance test reads this log back rather than parsing runner output: the
question is how many times the fixture was actually built, and by which
process.

Instance ids are PID-qualified because that is the whole question — the
contract is at most one instance per process, coordinator included.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    """Append one event line to the log named by ``PROC_COORD_LOG``."""
    with Path(f"{os.environ['PROC_COORD_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="process")
def engine() -> Iterator[str]:
    """One instance per process, coordinator included."""
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")


@oxi.fixture(lifetime="module")
def pinned() -> Iterator[str]:
    """Pins its modules to the coordinator, giving it a second phase.

    Every test module here names this fixture in ``@oxi.arrange``, which is
    what puts them in one Connected Component and runs that component on the
    coordinator. The declaration is explicit because #1848 retired the
    inference: the tier alone pins nothing, so an unarranged module-lifetime
    fixture would leave this project with a single phase.

    The pin is what the acceptance test rests on: two coordinator phases are
    what a per-phase process drain would rebuild ``engine`` between. With one
    phase the assertions cannot tell the fix from the defect.
    """
    _record(f"SHARED_SETUP {os.getpid()}")
    yield "pinned"
    _record(f"SHARED_TEARDOWN {os.getpid()}")
