"""One module, three consumers: the process tier, a builtin, and shared=True."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture, TempDirFactory


def _record(event: str) -> None:
    with Path(f"{os.environ['NEGATIVES_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


def test_alpha(
    per_process: Fixture[str],
    factory: TempDirFactory,
) -> None:
    factory.mktemp("x")
    # dirs is per-factory-instance state: it counts what *this* factory has
    # handed out. A factory rebuilt per task group always reads 1 here; one
    # hoisted to process lifetime would climb.
    _record(f"FACTORY alpha {os.getpid()} dirs={len(factory.dirs)}")
    assert per_process, "the process tier must inject"
