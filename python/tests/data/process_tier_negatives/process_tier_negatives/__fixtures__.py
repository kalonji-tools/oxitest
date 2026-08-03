"""The process tier, for contrast with the two task-scoped ones beside it."""

from __future__ import annotations

import itertools
import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    with Path(os.environ["NEGATIVES_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="process")
def per_process() -> Iterator[str]:
    _record(f"PROCESS_SETUP {os.getpid()}-{next(_COUNTER)}")
    yield "process"
