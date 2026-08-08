"""Async fixtures at both implemented lifetime tiers (kalonji-tools/oxitest#1733).

Every fixture records its own lifecycle to the file named by
``ASYNC_LIFETIMES_LOG`` so the acceptance tests can assert on what actually
happened rather than on how a reporter phrased it. A missing env value is a
hard error — otherwise every downstream assertion passes vacuously.

Four fixtures cover the two axes that matter: lifetime tier (function vs
module) x shape (plain coroutine vs async generator). The async generators
are what pin teardown to the right boundary; the plain coroutines are what
prove the value arrives awaited.
"""

from __future__ import annotations

import asyncio
import itertools
import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi

_COUNTER = itertools.count(1)


def _record(event: str) -> None:
    """Append one event line to the log named by ``ASYNC_LIFETIMES_LOG``."""
    path = os.environ["ASYNC_LIFETIMES_LOG"]
    with Path(f"{path}.{os.getpid()}").open("a", encoding="utf-8") as fh:
        fh.write(f"{event}\n")


def _next_id(kind: str) -> str:
    """PID-qualified instance id, so parallel runs cannot collide."""
    return f"{kind}-{os.getpid()}-{next(_COUNTER)}"


@oxi.fixture(lifetime="function")
async def per_test() -> str:
    """Plain coroutine, fresh per test. Proves the value arrives awaited."""
    await asyncio.sleep(0)
    instance_id = _next_id("per_test")
    _record(f"SETUP {instance_id}")
    return instance_id


@oxi.fixture(lifetime="function")
async def per_test_gen() -> AsyncIterator[str]:
    """Async generator, fresh per test. Teardown must fire after each test."""
    instance_id = _next_id("per_test_gen")
    _record(f"SETUP {instance_id}")
    yield instance_id
    await asyncio.sleep(0)
    _record(f"TEARDOWN {instance_id}")


@oxi.fixture(lifetime="module")
async def per_module() -> str:
    """Plain coroutine, one per module. Proves caching survives the await."""
    await asyncio.sleep(0)
    instance_id = _next_id("per_module")
    _record(f"SETUP {instance_id}")
    return instance_id


@oxi.fixture(lifetime="module")
async def per_module_gen() -> AsyncIterator[str]:
    """Async generator, one per module.

    Teardown must fire at the module boundary — not at session end, which is
    where an un-keyed ``SharedAsyncManager`` teardown list would put it.
    """
    instance_id = _next_id("per_module_gen")
    _record(f"SETUP {instance_id}")
    yield instance_id
    await asyncio.sleep(0)
    _record(f"TEARDOWN {instance_id}")
