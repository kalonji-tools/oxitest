"""One inprocess-marked async test in the declaring package, and one without."""

from __future__ import annotations

import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def _record_use(label: str, engine: str) -> None:
    with Path(f"{os.environ['SUBTREE_ASYNC_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"USE {label} {os.getpid()} {engine}\n")


@oxi.mark.inprocess
async def test_marked(engine: Fixture[str]) -> None:
    _record_use("marked", engine)
    assert engine, "the async package fixture must be injected into the marked test"


async def test_unmarked(engine: Fixture[str]) -> None:
    _record_use("unmarked", engine)
    assert engine, "the async package fixture must be injected into the unmarked test"
