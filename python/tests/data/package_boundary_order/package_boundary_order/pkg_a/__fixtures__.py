"""Package-lifetime fixture for the first of two sibling packages (#1839).

Its teardown must run when *this* package's last test finishes — before the
sibling package starts — not when the run ends. The fixture logs its own
setup and teardown so the acceptance test can assert on what actually
happened rather than on reporter output.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``P2LOG``."""
    with Path(os.environ["P2LOG"]).open("a") as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def engine_a() -> Iterator[str]:
    """One instance for pkg_a, disposed at pkg_a's boundary."""
    record("SETUP a")
    yield "a"
    record("TEARDOWN a")
