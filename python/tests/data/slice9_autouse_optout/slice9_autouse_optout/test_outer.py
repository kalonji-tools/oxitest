"""Outside the opt-out: the rootdir autouse fixture fires for this module."""

from __future__ import annotations

import os
from pathlib import Path


def _record(event: str) -> None:
    with Path(f"{os.environ['SLICE9_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


def test_outer_one() -> None:
    _record(f"TEST outer_one {os.getpid()}")
