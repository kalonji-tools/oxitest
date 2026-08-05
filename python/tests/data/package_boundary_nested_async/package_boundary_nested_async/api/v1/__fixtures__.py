"""The inner declaring package, whose async fixture holds the outer value.

Both anchors merge into one task group under ``api`` alone, so only ``api``'s
boundary is announced. Draining just that key would leave this scope for the
end-of-task backstop, and draining outermost-first would resume this
generator after the value it closes over had already been disposed.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def record(event: str) -> None:
    """Append one event line to the log named by ``NESTASYNCLOG``."""
    with Path(os.environ["NESTASYNCLOG"]).open("a") as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
async def inner(outer: Fixture[str]) -> AsyncIterator[str]:
    """Disposed before ``outer``, the value it holds a reference to."""
    record("SETUP inner")
    yield f"inner-of-{outer}"
    record(f"TEARDOWN inner sees {outer}")
