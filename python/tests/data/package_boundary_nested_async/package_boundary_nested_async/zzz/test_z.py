"""The only test in the unrelated sibling package."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(os.environ["NESTASYNCLOG"]).open("a", encoding="utf-8") as handle:
        handle.write(f"{event}\n")


async def test_z(fx: Fixtures) -> None:
    value = await fx.zzz.other
    assert value == "other", (
        f"the sibling async package fixture must resolve, got {value!r}"
    )
    record("USE other")
