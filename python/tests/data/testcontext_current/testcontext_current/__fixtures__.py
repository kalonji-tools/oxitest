"""A wide async fixture, so every async body is promoted onto the shared loop.

Promotion is the risky path: the body runs as a Task on a session-lifetime
event loop rather than inline, so this project is the only place the
ContextVar's survival across that hop is actually exercised.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path

import oxitest as oxi
from oxitest import TestContext
from oxitest._bridge._errors import TestContextUnavailableError


def _record(event: str) -> None:
    """Append one event line to the log named by ``TCC_LOG``."""
    with Path(os.environ["TCC_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


@contextmanager
def _records(label: str) -> Iterator[None]:
    """Record whether the block refused or returned, tagged with *label*.

    ``else`` rather than a trailing line inside ``try``: an exception raised by
    ``_record`` itself would otherwise be caught here and mislabelled as the
    position having refused.
    """
    try:
        yield
    except TestContextUnavailableError:
        _record(f"{label} raised")
    else:
        _record(f"{label} returned")


@oxi.fixture(lifetime="process")
async def aengine() -> AsyncIterator[str]:
    """One instance per process, recording what current() does at both ends."""
    with _records("FIXTURE_SETUP"):
        TestContext.current()
    yield "engine"
    with _records("WIDE_TEARDOWN"):
        TestContext.current()
