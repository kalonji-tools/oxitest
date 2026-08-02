from __future__ import annotations

from oxitest import Fixture, TempDirFactory


def test_six(exploding: Fixture[str], factory: TempDirFactory) -> None:
    # The factory is session-scoped, so --keep-tmp makes its teardown emit a
    # NOTICE at end_session. That exercises the non-teardown-failure half of
    # what #1840 loses; which drain picks it up depends on whether this module
    # lands in a worker's final task group.
    factory.mktemp("kept")
    assert exploding == "value", (
        "the fixture must deliver its value — this test exists only to force "
        "the module-lifetime teardown whose diagnostic #1840 loses"
    )
