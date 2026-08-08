"""Async twin of the nested-anchor project: the outer declaring package."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``NESTASYNCLOG``."""
    with Path(os.environ["NESTASYNCLOG"]).open("a", encoding="utf-8") as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
async def outer() -> AsyncIterator[str]:
    """Spans api and everything beneath it, including api/v1."""
    record("SETUP outer")
    yield "outer"
    record("TEARDOWN outer")
