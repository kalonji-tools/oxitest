"""Inside the opt-out: neither declaration fires, because neither is autouse here."""

from __future__ import annotations

import os
from pathlib import Path


def _record(event: str) -> None:
    with Path(os.environ["SLICE9_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


def test_inner_one() -> None:
    _record(f"TEST inner_one {os.getpid()}")
