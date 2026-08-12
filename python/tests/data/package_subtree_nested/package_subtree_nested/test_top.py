"""A module in the outer package only — no mark of its own."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture


def test_top(outer: Fixture[str]) -> None:
    with Path(f"{os.environ['SUBTREE_NESTED_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE top {os.getpid()} {outer}\n")
    assert outer, "the outer package fixture must be injected"
