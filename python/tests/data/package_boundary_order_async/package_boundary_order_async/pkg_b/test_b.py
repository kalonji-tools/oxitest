"""pkg_b's only test, touching both async registration routes at once."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture, Fixtures


def _record(event: str) -> None:
    with Path(f"{os.environ['ASYNCLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


async def test_b(fx: Fixtures, eager_b: Fixture[str]) -> None:
    lazy = await fx.pkg_b.lazy_b
    assert lazy == "b-lazy", (
        f"the proxy-route async package fixture must resolve, got {lazy!r}"
    )
    assert eager_b == "b-eager", (
        f"the injected async package fixture must resolve, got {eager_b!r}"
    )
    _record("USE b")
