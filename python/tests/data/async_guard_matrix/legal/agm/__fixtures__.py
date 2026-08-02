"""Async fixtures at all four ADR-0009 lifetime tiers.

Consumed by ``test_matrix.py`` through the ``Fixture[T]`` parameter route only
— the proxy route already has coverage in
``python/tests/data/async_lifetimes/``. What is uncovered, and what #1876
proposed to change, is the parameter route's answer for a **sync** test.

ADR-0006's dispatch table says an async fixture at ``shared``/``session``
scope is resolved by ``SharedAsyncManager.resolve()`` on the shared session
loop, i.e. before the test starts, and that dispatch is not conditioned on the
test's kind. Only ``each`` (``function``) lifetime needs the test's own loop.
So three of these four are reachable from a sync test, and the fourth is
ADR-0006's one illegal cell — covered in ``../illegal/``.
"""

from __future__ import annotations

import asyncio

import oxitest as oxi

from agm._kinds import Fn, Mod, Pkg, Sess


@oxi.fixture(lifetime="function")
async def per_function() -> Fn:
    await asyncio.sleep(0)
    return Fn()


@oxi.fixture(lifetime="module")
async def per_module() -> Mod:
    await asyncio.sleep(0)
    return Mod()


@oxi.fixture(lifetime="package")
async def per_package() -> Pkg:
    await asyncio.sleep(0)
    return Pkg()


@oxi.fixture(lifetime="session")
async def per_session() -> Sess:
    await asyncio.sleep(0)
    return Sess()
