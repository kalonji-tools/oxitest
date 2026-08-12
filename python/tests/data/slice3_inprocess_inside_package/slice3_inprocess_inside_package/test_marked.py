"""One inprocess-marked test inside the declaring package, and one without.

The mark is what splits the subtree if the planner lets it. Both tests record
which instance they observed, so the acceptance test can assert they shared one.
"""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def _record_use(label: str, engine: str) -> None:
    with Path(f"{os.environ['SLICE3_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE {label} {os.getpid()} {engine}\n")


@oxi.mark.inprocess
def test_marked(engine: Fixture[str]) -> None:
    _record_use("marked", engine)
    assert engine, "the package fixture must be injected into the marked test"


def test_unmarked(engine: Fixture[str]) -> None:
    _record_use("unmarked", engine)
    assert engine, "the package fixture must be injected into the unmarked test"
