"""The unarranged sibling — the half that used to be stranded on a worker."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture


def test_plain(engine: Fixture[str]) -> None:
    with Path(f"{os.environ['SUBTREE_ARRANGED_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE plain {os.getpid()} {engine}\n")
    assert engine, "the package fixture must be injected into the unarranged test"
