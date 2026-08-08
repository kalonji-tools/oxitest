"""Arranged module — pinned to the coordinator by its shared-fixture use."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture


def test_alpha(engine: Fixture[str], pinned: Fixture[str]) -> None:
    with Path(f"{os.environ['PROC_COORD_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE alpha {os.getpid()} {engine}\n")
    assert engine, "the process-lifetime fixture must be injected"
    assert pinned == "pinned", "the shared fixture must be injected"
