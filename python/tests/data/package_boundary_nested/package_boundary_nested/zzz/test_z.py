"""The only test in the unrelated sibling package."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def record(event: str) -> None:
    with Path(os.environ["NESTLOG"]).open("a", encoding="utf-8") as handle:
        handle.write(f"{event}\n")


def test_z(fx: Fixtures) -> None:
    assert fx.zzz.other == "other", "the sibling package fixture must be injected"
    record("USE other")
