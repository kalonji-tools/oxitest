"""The inner declaring package of a nested pair.

Both anchors merge into one task group under ``api`` alone, so only ``api``'s
boundary is announced. Draining just that key would leave this scope for the
end-of-task backstop, and draining outermost-first would resume this
generator after the ancestor it is nested inside had already been disposed.

Deliberately does *not* take ``outer`` as a dependency. An async fixture
resolving another async fixture lazily, from inside a test already running on
the session loop, fails with "This event loop is already running" — so a
dependency here would make this project pass or fail on test order rather than
on the boundary behaviour it exists to pin. The nesting is structural, which
is what the drain order is derived from; the dependency added nothing.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``NESTASYNCLOG``."""
    with Path(f"{os.environ['NESTASYNCLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
async def inner() -> AsyncIterator[str]:
    """Disposed before ``outer``, the package it is nested inside."""
    record("SETUP inner")
    yield "inner"
    record("TEARDOWN inner")
