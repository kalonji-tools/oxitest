"""The inprocess phase — the coordinator's other serial phase.

Arranges ``pinned`` as well as taking ``engine`` deliberately. Without it the
module-lifetime fixture is resolved only in the arranged phase, and the
cross-phase assertion in the acceptance test has one build to look at instead
of two — it cannot then tell a paired build/teardown cycle from a value reused
after its own teardown.
"""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


@oxi.mark.inprocess
@oxi.arrange("pinned")
def test_marked(engine: Fixture[str]) -> None:
    with Path(f"{os.environ['PROC_COORD_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE marked {os.getpid()} {engine}\n")
    assert engine, "the process-lifetime fixture must be injected"
