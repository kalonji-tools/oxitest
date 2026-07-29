"""A sync test reaching a function-lifetime async fixture through ``fx.``.

Per ADR-0006 this is the one illegal cell, and it must be rejected loudly at
access with three legal exits — not silently handed a coroutine, which is the
behaviour kalonji-tools/oxitest#1733 exists to remove.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_sync_test_cannot_reach_async_fixture(fx: Fixtures) -> None:
    value = fx.async_sync_reject.needs_a_loop
    assert value is not None, "unreachable — access above must have raised"
