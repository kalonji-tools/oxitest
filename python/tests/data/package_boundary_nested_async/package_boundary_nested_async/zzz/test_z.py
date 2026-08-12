"""The only test in the unrelated sibling package."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(f"{os.environ['NESTASYNCLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


async def test_z(fx: Fixtures) -> None:
    value = await fx.zzz.other
    assert value == "other", (
        f"the sibling async package fixture must resolve, got {value!r}"
    )
    _record("USE other")
