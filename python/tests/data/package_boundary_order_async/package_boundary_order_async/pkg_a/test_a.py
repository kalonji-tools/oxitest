"""pkg_a's only test, touching both async registration routes at once."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture, Fixtures


def record(event: str) -> None:
    with Path(f"{os.environ['ASYNCLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


async def test_a(fx: Fixtures, eager_a: Fixture[str]) -> None:
    lazy = await fx.pkg_a.lazy_a
    assert lazy == "a-lazy", (
        f"the proxy-route async package fixture must resolve, got {lazy!r}"
    )
    assert eager_a == "a-eager", (
        f"the injected async package fixture must resolve, got {eager_a!r}"
    )
    record("USE a")
