"""A test in the outer package only."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(f"{os.environ['NESTLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


def test_outer(fx: Fixtures) -> None:
    assert fx.api.outer == "outer", "the outer package fixture must be injected"
    record("USE outer")
