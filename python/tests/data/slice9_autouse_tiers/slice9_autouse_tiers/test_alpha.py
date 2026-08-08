"""Tests that request nothing — every fixture they exercise is autouse.

The log helper is duplicated per module rather than imported: oxitest is
invoked with this project as a positional path, so the package is not
importable by name from the caller's sys.path.
"""

from __future__ import annotations

import os
from pathlib import Path


def _record(event: str) -> None:
    with Path(f"{os.environ['SLICE9_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


def test_alpha_one() -> None:
    _record(f"TEST alpha_one {os.getpid()}")


def test_alpha_two() -> None:
    _record(f"TEST alpha_two {os.getpid()}")
