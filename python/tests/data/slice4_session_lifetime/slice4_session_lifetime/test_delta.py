"""Tests sharing the session-lifetime instance declared in ``__fixtures__.py``.

The log helper is duplicated per module rather than imported: oxitest is
invoked with this project as a positional path, so the package is not
importable by name from the caller's sys.path.

The USE line records the running PID as well as the instance id — the slice-4
assertion is a per-PID relation, not a global count, so the log has to name
the process that observed each value.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(os.environ["SLICE4_LOG"]).open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


def _use(fx: Fixtures) -> None:
    engine = fx.slice4_session_lifetime.engine
    assert engine is not None, "the session-lifetime fixture must be injected"
    _record(f"USE delta {os.getpid()} {engine}")


def test_delta_one(fx: Fixtures) -> None:
    _use(fx)


def test_delta_two(fx: Fixtures) -> None:
    _use(fx)
