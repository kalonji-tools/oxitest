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
    with Path(os.environ["PROC_COORD_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="process")
def engine() -> Iterator[str]:
    """One instance per process, coordinator included."""
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
