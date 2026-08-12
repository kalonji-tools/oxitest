"""A test in the inner package, which pulls both tiers into existence."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(f"{os.environ['NESTLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


def test_inner(fx: Fixtures) -> None:
    assert fx.v1.inner == "inner-of-outer", (
        "the inner package fixture must resolve, and must have been built on "
        "the outer package's value"
    )
    _record("USE inner")
