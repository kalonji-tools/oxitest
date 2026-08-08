"""The inprocess phase — the coordinator's other serial phase.

Takes ``pinned`` as well as ``engine`` deliberately. Without it the shared
fixture is resolved only in the arranged phase, and the cross-phase assertion
in the acceptance test has one build to look at instead of two — it cannot then
tell a paired build/teardown cycle from a value reused after its own teardown.
"""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


@oxi.mark.inprocess
def test_marked(engine: Fixture[str], pinned: Fixture[str]) -> None:
    with Path(os.environ["PROC_COORD_LOG"]).open("a", encoding="utf-8") as fh:
        fh.write(f"USE marked {os.getpid()} {engine}\n")
    assert engine, "the process-lifetime fixture must be injected"
    assert pinned == "pinned", "the shared fixture must be injected"
