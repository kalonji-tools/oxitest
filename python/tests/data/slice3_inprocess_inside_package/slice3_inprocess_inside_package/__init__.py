"""A package-lifetime fixture in a package that also marks a test inprocess.

The combination is honoured by keeping the whole subtree in one dispatch
phase (#2058). A marked test anywhere under the anchor pulls every module
beneath it onto the coordinator, so one session builds the fixture and the
exactly-once guarantee holds.

Before #2058 this project existed to prove the opposite: collection refused
the combination, because the marked items travelled to the coordinator while
their siblings went to a worker and the fixture was built once in each.

Each build appends a PID-qualified instance id, so a rebuild inside one
process is distinguishable from a build in another.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    """Append one event line to the log named by ``SLICE3_LOG``."""
    with Path(f"{os.environ['SLICE3_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def engine() -> Iterator[str]:
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
