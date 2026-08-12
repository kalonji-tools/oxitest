"""The only test in the unrelated sibling package."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(f"{os.environ['NESTLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


def test_z(fx: Fixtures) -> None:
    assert fx.zzz.other == "other", "the sibling package fixture must be injected"
    _record("USE other")
