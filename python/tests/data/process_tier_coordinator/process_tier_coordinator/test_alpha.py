"""Arranged module — pinned to the coordinator by an explicit @oxi.arrange."""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


@oxi.arrange("pinned")
def test_alpha(engine: Fixture[str]) -> None:
    with Path(f"{os.environ['PROC_COORD_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE alpha {os.getpid()} {engine}\n")
    assert engine, "the process-lifetime fixture must be injected"
