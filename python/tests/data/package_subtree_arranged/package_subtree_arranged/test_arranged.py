"""The arranged module — the half that forms a component."""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


@oxi.arrange("pinned")
def test_arranged(engine: Fixture[str]) -> None:
    with Path(f"{os.environ['SUBTREE_ARRANGED_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE arranged {os.getpid()} {engine}\n")
    assert engine, "the package fixture must be injected into the arranged test"
