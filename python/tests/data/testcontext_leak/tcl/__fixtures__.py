"""A failing function fixture plus a process fixture that probes at teardown.

The failing fixture makes ``run_test`` take its one early-return path, which
sits outside the try/finally that resets the identity ContextVar. If that
path does not reset, the process fixture's teardown — which runs after every
test is over — sees a test that never ran.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import oxitest as oxi
from oxitest import TestContext
from oxitest._bridge._errors import TestContextUnavailableError


def _record(event: str) -> None:
    """Append one event line to the log named by ``TCL_LOG``."""
    with Path(f"{os.environ['TCL_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="function")
def boom() -> Iterator[str]:
    """Always fails during setup, forcing run_test's early return."""
    msg = "fixture setup fails on purpose"
    raise RuntimeError(msg)
    yield "never"


@oxi.fixture(lifetime="process")
def probe() -> Iterator[str]:
    """Reports what current() does from a wide teardown, after the failure."""
    yield "p"
    try:
        ctx = TestContext.current()
        _record(f"WIDE_TEARDOWN returned {ctx.node_id}")
    except TestContextUnavailableError:
        _record("WIDE_TEARDOWN raised")
