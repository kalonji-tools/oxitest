"""The inprocess phase — the coordinator's other serial phase."""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


@oxi.mark.inprocess
def test_marked(engine: Fixture[str]) -> None:
    with Path(os.environ["PROC_COORD_LOG"]).open("a") as fh:
        fh.write(f"USE marked {os.getpid()} {engine}\n")
    assert engine, "the process-lifetime fixture must be injected"
