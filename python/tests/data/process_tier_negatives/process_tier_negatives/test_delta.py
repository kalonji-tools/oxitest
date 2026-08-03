"""One module, three consumers: the process tier, a builtin, and shared=True."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture, TempDirFactory


def _record(event: str) -> None:
    with Path(os.environ["NEGATIVES_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


def test_delta(
    per_process: Fixture[str],
    legacy_shared: Fixture[str],
    factory: TempDirFactory,
) -> None:
    factory.mktemp("x")
    # dirs is per-factory-instance state: it counts what *this* factory has
    # handed out. A factory rebuilt per task group always reads 1 here; one
    # hoisted to process lifetime would climb.
    _record(f"FACTORY delta {os.getpid()} dirs={len(factory.dirs)}")
    assert per_process and legacy_shared, "both wide tiers must inject"
