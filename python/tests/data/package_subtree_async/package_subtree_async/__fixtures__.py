"""Async twin of the inprocess-inside-a-package project.

The two rules that keep a declaring subtree in one phase partition
``ModuleGroup``s and never touch fixture construction, so an async fixture
should behave exactly like a sync one. That is an argument, not a measurement,
and every arm that established #2058's rules built a sync fixture. This project
is the measurement.
"""

from __future__ import annotations

import itertools
import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    with Path(f"{os.environ['SUBTREE_ASYNC_LOG']}.{os.getpid()}").open(
        "a", encoding="utf-8"
    ) as fh:
        fh.write(f"{event}\n")


@oxi.fixture(lifetime="package")
async def engine() -> AsyncIterator[str]:
    instance_id = f"{os.getpid()}-{next(_COUNTER)}"
    _record(f"SETUP {instance_id}")
    yield instance_id
    _record(f"TEARDOWN {instance_id}")
