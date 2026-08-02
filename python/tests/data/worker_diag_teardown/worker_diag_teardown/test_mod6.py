from __future__ import annotations

from oxitest import Fixture


def test_six(exploding: Fixture[str]) -> None:
    assert exploding == "value", (
        "the fixture must deliver its value — this test exists only to force "
        "the module-lifetime teardown whose diagnostic #1840 loses"
    )
