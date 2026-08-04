"""Tests that request nothing at all — the process fixture is autouse.

The USE line records the running PID so the acceptance test can derive how many
processes actually executed a test. The third field is unused here: no test
names the fixture, so there is no instance id to report, and inventing one
would mean requesting the fixture and changing what is under test.

The log helper is duplicated per module rather than imported: oxitest is
invoked with this project as a positional path, so the package is not
importable by name from the caller's sys.path.
"""

from __future__ import annotations

import os
from pathlib import Path


def _record(event: str) -> None:
    with Path(os.environ["SLICE9_PROC_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


def test_gamma_one() -> None:
    _record(f"USE gamma {os.getpid()} unrequested")


def test_gamma_two() -> None:
    _record(f"USE gamma {os.getpid()} unrequested")
