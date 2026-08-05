"""A test in the inner package, which pulls the nested tier into existence."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(os.environ["NESTASYNCLOG"]).open("a") as handle:
        handle.write(f"{event}\n")


async def test_inner(fx: Fixtures) -> None:
    value = await fx.v1.inner
    assert value == "inner", (
        f"the inner async package fixture must resolve, got {value!r}"
    )
    record("USE inner")
