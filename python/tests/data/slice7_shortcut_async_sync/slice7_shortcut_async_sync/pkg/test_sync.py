"""A sync test reaching an async fixture by shortcut.

Expected to error with ``AsyncFixtureAccessError`` at the access itself, before
the factory runs — the same verdict the qualified route gives. The runner-level
assertion lives in ``python/tests/test_fixtures_redesign_slice7.py``.

The async test alongside it is the positive control: it proves the fixture is
registered and awaitable, so the sync failure cannot be blamed on a fixture
that was never there.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_a_sync_test_cannot_shortcut_to_an_async_fixture(fx: Fixtures) -> None:
    # Act — expected to raise AsyncFixtureAccessError, not to return
    value = fx.conn

    # Assert — unreachable; reaching it means a coroutine leaked into a sync test
    assert value is None, (
        "reaching this line means the shortcut handed a sync test an "
        "un-awaited coroutine — exactly the silent failure #1733 removed from "
        "the qualified route"
    )


async def test_an_async_test_may_shortcut_to_the_same_fixture(fx: Fixtures) -> None:
    # Act
    value = await fx.conn

    # Assert
    assert value.label == "async-function-scope", (
        "without this passing, the sync test's error could equally be a "
        "registration failure rather than the async guard firing"
    )
