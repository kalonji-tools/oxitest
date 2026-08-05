"""A test in the outer package only."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(os.environ["NESTLOG"]).open("a") as handle:
        handle.write(f"{event}\n")


def test_outer(fx: Fixtures) -> None:
    assert fx.api.outer == "outer", "the outer package fixture must be injected"
    record("USE outer")
