"""Module-lifetime fixture that records its own lifecycle to a file.

The acceptance tests read the log back rather than parsing runner output, so
they assert on what the fixture actually did, not on how a reporter phrased it.
``SLICE2_LOG`` is set by the calling test; a missing value is a hard error
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
    """Append one event line to the log named by ``SLICE2_LOG``."""
    path = os.environ["SLICE2_LOG"]
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="module")
def resource() -> Iterator[str]:
    """One instance per test module, disposed after that module's last test."""
    # PID-qualified so parallel runs cannot produce colliding ids across workers.
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
