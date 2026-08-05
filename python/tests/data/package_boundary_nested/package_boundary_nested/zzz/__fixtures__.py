"""An unrelated sibling package, run after the nested pair.

Without it the api boundary and the end of the run coincide, and a scope that
survived to the end-of-task backstop would be indistinguishable from one
disposed correctly at its boundary.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``NESTLOG``."""
    with Path(os.environ["NESTLOG"]).open("a") as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def other() -> Iterator[str]:
    """Its SETUP is the marker that api's boundary has already passed."""
    record("SETUP other")
    yield "other"
    record("TEARDOWN other")
