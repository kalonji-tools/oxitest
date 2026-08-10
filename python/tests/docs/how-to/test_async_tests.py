"""Tested examples for the use-async-tests how-to guide."""

import asyncio
from collections.abc import AsyncGenerator

import oxitest
from oxitest import Fixture, fixture


# fmt: off
# --8<-- [start:basic-async]
async def test_sleep_completes() -> None:
    await asyncio.sleep(0)
    assert True, "async test should complete"
# --8<-- [end:basic-async]

# fmt: on


class _StubPool:
    """Stub pool for doc examples."""

    async def close(self) -> None:
        await asyncio.sleep(0)


async def create_pool(url: str) -> _StubPool:
    """Stub — returns a mock pool."""
    await asyncio.sleep(0)
    return _StubPool()


# fmt: off
# --8<-- [start:shared-async-fixture]
@fixture(lifetime="module")
async def db_pool() -> AsyncGenerator[object, None]:
    pool = await create_pool("postgresql://localhost/testdb")
    yield pool
    await pool.close()
# --8<-- [end:shared-async-fixture]

# --8<-- [start:task-group]
async def test_concurrent_work(task_group: Fixture[asyncio.TaskGroup]) -> None:
    results: list[int] = []

    async def worker(n: int) -> None:
        results.append(n)

    task_group.create_task(worker(1))
    task_group.create_task(worker(2))
    await asyncio.sleep(0)  # let tasks run
    assert sorted(results) == [1, 2], "both workers should complete"
# --8<-- [end:task-group]

# --8<-- [start:async-marks]
@oxitest.mark.skip(reason="not implemented yet")
async def test_pending() -> None:
    pass


@oxitest.mark.xfail(reason="known bug #42")
async def test_known_failure() -> None:
    assert False  # noqa: B011 — intentional xfail


@oxitest.mark.timeout(seconds=5)
async def test_within_budget() -> None:
    await asyncio.sleep(0.1)
# --8<-- [end:async-marks]
# fmt: on
