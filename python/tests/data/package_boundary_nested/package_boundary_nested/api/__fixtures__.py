"""The outer declaring package of a nested pair (#1839).

``api`` and ``api/v1`` both declare a package-lifetime fixture, so the
scheduler merges the whole subtree into one task group under the *outermost*
anchor — ``outermost_declaring_ancestor`` keeps only ``api``. The boundary
that ends ``api`` must therefore end ``api/v1`` too, and must end it first.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``NESTLOG``."""
    with Path(f"{os.environ['NESTLOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
def outer() -> Iterator[str]:
    """Spans api and everything beneath it, including api/v1."""
    record("SETUP outer")
    yield "outer"
    record("TEARDOWN outer")
