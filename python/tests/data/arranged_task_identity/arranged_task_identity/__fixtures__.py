"""Fixtures that record which task each lifecycle phase runs in (#1740).

Every fixture writes its own lifecycle to the file named by
``TASK_IDENTITY_LOG``, so the acceptance tests assert on what happened rather
than on how a reporter phrased it.

Task **names** rather than ``id()``: CPython can give a freed task's address to
the next task, so three sequential tasks can report one address. A first probe
on this issue read "one task" from exactly that reuse.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import oxitest as oxi


def _record(event: str) -> None:
    """Append one event line to the log named by ``TASK_IDENTITY_LOG``."""
    path = os.environ["TASK_IDENTITY_LOG"]
    with Path(f"{path}.{os.getpid()}").open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


def _task_name() -> str:
    """Name of the running task. Names come from a monotonic counter."""
    task = asyncio.current_task()
    return task.get_name() if task is not None else "none"


@oxi.fixture(lifetime="function")
def sync_arranged() -> Iterator[str]:
    _record("1 SYNC-SETUP")
    yield "s"
    _record("9 SYNC-TEARDOWN")


@oxi.fixture(lifetime="function")
async def async_arranged() -> AsyncIterator[str]:
    var: contextvars.ContextVar[str] = contextvars.ContextVar("v", default="unset")
    var.set("set-in-setup")
    _record(f"2 ARRANGED-SETUP task={_task_name()} reads={var.get()}")
    yield "a"
    _record(f"8 ARRANGED-TEARDOWN task={_task_name()} reads={var.get()}")


@oxi.fixture(lifetime="function")
async def param_fixture() -> AsyncIterator[contextvars.ContextVar[str]]:
    var: contextvars.ContextVar[str] = contextvars.ContextVar("v", default="unset")
    var.set("set-in-setup")
    _record(f"3 PARAM-SETUP task={_task_name()} reads={var.get()}")
    yield var
    _record(f"7 PARAM-TEARDOWN task={_task_name()} reads={var.get()}")


@oxi.fixture(lifetime="function")
async def plain_coroutine() -> str:
    _record(f"C PLAIN-SETUP task={_task_name()}")
    return "c"


@oxi.fixture(lifetime="function")
async def failing() -> AsyncIterator[str]:
    msg = "arranged async setup failed on purpose"
    raise RuntimeError(msg)
    yield "never"


@oxi.fixture(lifetime="function")
async def param_fixture_two() -> AsyncIterator[str]:
    """A second parameter fixture, so the parameter drain has two entries.

    One entry cannot detect a lost ``reversed()``. A mutant that dropped it
    from the parameter drain survived until this fixture existed.
    """
    _record(f"4 PARAM2-SETUP task={_task_name()}")
    yield "p2"
    _record(f"6 PARAM2-TEARDOWN task={_task_name()}")
