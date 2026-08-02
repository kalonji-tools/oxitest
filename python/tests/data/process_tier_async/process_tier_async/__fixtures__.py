"""An **async** process-lifetime fixture, logging every build and disposal.

The sync half of this contract is covered by ``slice4_session_lifetime``. The
async half runs through a different machinery entirely — ``SharedAsyncManager``
holds the pending generators and the event loop — and had no acceptance project
at any wide tier before #1777.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    """Append one event line to the log named by ``PROC_ASYNC_LOG``."""
    with Path(os.environ["PROC_ASYNC_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="session")
async def aengine() -> AsyncIterator[str]:
    """One instance per process, torn down once, after that process's last test."""
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
