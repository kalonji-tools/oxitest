"""pkg_b's async twin of pkg_a — same two registration routes, same reason."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``ASYNCLOG``."""
    with Path(os.environ["ASYNCLOG"]).open("a", encoding="utf-8") as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
async def lazy_b() -> AsyncIterator[str]:
    """Reached by ``await fx.pkg_b.lazy_b`` — the register_teardown site."""
    record("SETUP b-lazy")
    yield "b-lazy"
    record("TEARDOWN b-lazy")


@oxi.fixture(lifetime="package")
async def eager_b() -> AsyncIterator[str]:
    """Reached by ``Fixture[T]`` injection — the resolve site."""
    record("SETUP b-eager")
    yield "b-eager"
    record("TEARDOWN b-eager")
