"""Package-lifetime fixture for the second of two sibling packages (#1839).

The sibling is what makes the boundary observable: a single-package suite
cannot tell "disposed at the package boundary" apart from "disposed at the
end of the run", because for it the two moments coincide.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``P2LOG``."""
    with Path(f"{os.environ['P2LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def engine_b() -> Iterator[str]:
    """One instance for pkg_b, disposed at pkg_b's boundary."""
    record("SETUP b")
    yield "b"
    record("TEARDOWN b")
