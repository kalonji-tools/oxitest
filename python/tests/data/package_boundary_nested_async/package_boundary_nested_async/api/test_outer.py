"""A test in the outer package only."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(os.environ["NESTASYNCLOG"]).open("a") as handle:
        handle.write(f"{event}\n")


async def test_outer(fx: Fixtures) -> None:
    value = await fx.api.outer
    assert value == "outer", (
        f"the outer async package fixture must resolve, got {value!r}"
    )
    record("USE outer")
