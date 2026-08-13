"""Phase order and task identity across the arranged and parameter routes.

The log helper is duplicated here rather than imported from ``__fixtures__``:
oxitest loads that file under a synthetic module name, so importing it by
package name would give a second module object with its own module-level state
(kalonji-tools/oxitest#1740).
"""

from __future__ import annotations

import asyncio
import contextvars
import os
from pathlib import Path

import oxitest as oxi
from oxitest import Fixture


def _record(event: str) -> None:
    path = os.environ["TASK_IDENTITY_LOG"]
    with Path(f"{path}.{os.getpid()}").open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


def _task_name() -> str:
    task = asyncio.current_task()
    return task.get_name() if task is not None else "none"


@oxi.arrange("sync_arranged", "async_arranged")
async def test_ordering(
    param_fixture: Fixture[contextvars.ContextVar[str]],
    param_fixture_two: Fixture[str],
) -> None:
    _record(f"5 BODY task={_task_name()} reads={param_fixture.get()}")
    param_fixture.set("set-in-body")
    assert param_fixture_two == "p2", "the second parameter fixture must arrive awaited"
