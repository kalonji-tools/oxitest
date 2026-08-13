"""A plain-coroutine arranged fixture registers no teardown.

The log helper is duplicated here for the reason ``test_order.py`` gives.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import oxitest as oxi


def _record(event: str) -> None:
    path = os.environ["TASK_IDENTITY_LOG"]
    with Path(f"{path}.{os.getpid()}").open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


@oxi.arrange("plain_coroutine")
async def test_plain_coroutine_arranged() -> None:
    task = asyncio.current_task()
    name = task.get_name() if task is not None else "none"
    _record(f"C BODY task={name}")
