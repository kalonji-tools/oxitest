"""Package-lifetime fixture declared in ``__init__.py`` (ADR-0009 slice 3).

Two things are under test here at once. The fixture is declared in
``__init__.py`` rather than ``__fixtures__.py`` — the second declaration home
from ADR-0009's file-convention table — and it carries ``lifetime="package"``,
so exactly one instance must serve every module in this package and its
subdirectories, including under parallel execution.

The acceptance test reads the log back rather than parsing runner output, so it
asserts on what the fixture actually did. ``SLICE3_LOG`` is set by the calling
test; a missing value is a hard error because every assertion downstream would
otherwise pass vacuously.
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
    path = os.environ["SLICE3_LOG"]
    with Path(f"{path}.{os.getpid()}").open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def engine() -> Iterator[str]:
    """One instance for this package and every directory beneath it."""
    # PID-qualified so a second instance built in another worker is visibly
    # distinct rather than colliding with the first.
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
