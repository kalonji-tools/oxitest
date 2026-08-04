"""Autouse fixtures at all four lifetime tiers (ADR-0009 slice 9, #1716).

Declaration order here is deliberately **narrowest first**. Firing order is
widest-lifetime-first, so a run that reports these in declaration order has
lost the ordering rule — and registration order is what the previous
implementation yielded, which is why the wrong answer is the plausible one.

Every fixture logs ``FIRE <name> <pid>`` when it builds. The generic
``SETUP <pid>-<n>`` convention is not used because there are four distinct
fixtures here and that convention carries no fixture name; ``EventLogRun.lines``
reads these directly, which is what its docstring prescribes for exactly this
case.

``SLICE9_LOG`` is set by the calling test. A missing value is a hard error,
because every assertion downstream would otherwise pass vacuously.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi


def _record(event: str) -> None:
    """Append one event line to the log named by ``SLICE9_LOG``."""
    with Path(os.environ["SLICE9_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="function", autouse=True)
def per_test() -> Iterator[str]:
    """Fires once per test in its B1 boundary."""
    _record(f"FIRE per_test {os.getpid()}")
    yield "per_test"


@oxi.fixture(lifetime="module", autouse=True)
def per_module() -> Iterator[str]:
    """Fires once per module boundary — the scope cache collapses the rest."""
    _record(f"FIRE per_module {os.getpid()}")
    yield "per_module"


@oxi.fixture(lifetime="package", autouse=True)
def per_package() -> Iterator[str]:
    """Rootdir package: the only exactly-once-per-run tier."""
    _record(f"FIRE per_package {os.getpid()}")
    yield "per_package"


@oxi.fixture(lifetime="process", autouse=True)
def per_process() -> Iterator[str]:
    """Once per process that resolves it — rootdir-only, per Rule 4."""
    _record(f"FIRE per_process {os.getpid()}")
    yield "per_process"
