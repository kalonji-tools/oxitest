"""The only test in pkg_a — its completion is pkg_a's package boundary."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(os.environ["P2LOG"]).open("a") as handle:
        handle.write(f"{event}\n")


def test_a(fx: Fixtures) -> None:
    engine = fx.pkg_a.engine_a
    assert engine == "a", f"pkg_a's package fixture must be injected, got {engine!r}"
    record("USE a")
