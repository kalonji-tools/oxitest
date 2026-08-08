"""One module, one async test — four modules make four task groups."""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixture


def _record(event: str) -> None:
    """Append one event line. A sync helper because the test is async def.

    The blocking write is deliberate — this project asserts on event *order*,
    not on concurrency — but a bare open inside an async body is a lint
    error, and rightly so.
    """
    with Path(f"{os.environ['PROC_ASYNC_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


async def test_alpha(aengine: Fixture[str]) -> None:
    _record(f"USE alpha {os.getpid()} {aengine}")
    assert aengine, "the async process-lifetime fixture must be injected"
