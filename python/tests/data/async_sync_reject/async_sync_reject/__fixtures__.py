"""One async fixture, reached from a sync test — the ADR-0006 illegal cell."""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
async def needs_a_loop() -> str:
    return "unreachable-from-a-sync-test"
