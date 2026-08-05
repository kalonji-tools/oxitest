"""The only test in pkg_b — it must not start while pkg_a is still held."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(os.environ["P2LOG"]).open("a") as handle:
        handle.write(f"{event}\n")


def test_b(fx: Fixtures) -> None:
    engine = fx.pkg_b.engine_b
    assert engine == "b", f"pkg_b's package fixture must be injected, got {engine!r}"
    record("USE b")
