"""An autouse fixture a nested package opts out of (ADR-0009 slice 9, #1716).

``setup`` is autouse here and fires for every module in the tree — except where
a deeper anchor declares a fixture of the same name without ``autouse``. That
shadowing is boundary-local: the deeper declaration wins inside its own
subtree and is invisible outside it, so the autouse fixture keeps firing
everywhere else.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


def _record(event: str) -> None:
    with Path(f"{os.environ['SLICE9_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="module", autouse=True)
def setup() -> Iterator[str]:
    """Fires per module boundary, everywhere it is not shadowed."""
    _record(f"FIRE setup {os.getpid()}")
    yield "setup"
