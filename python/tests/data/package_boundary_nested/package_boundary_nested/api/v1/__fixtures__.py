"""The inner declaring package, whose fixture depends on the outer one.

The dependency is the point. B1 lets a fixture resolve an ancestor package's
fixture, so disposing ``api`` before ``api/v1`` would run this teardown
against a value that had already been torn down.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def record(event: str) -> None:
    """Append one event line to the log named by ``NESTLOG``."""
    with Path(os.environ["NESTLOG"]).open("a") as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def inner(outer: Fixture[str]) -> Iterator[str]:
    """Disposed before ``outer``, the value it holds a reference to."""
    record("SETUP inner")
    yield f"inner-of-{outer}"
    record(f"TEARDOWN inner sees {outer}")
