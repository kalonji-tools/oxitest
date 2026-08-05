from __future__ import annotations

from oxitest import Fixture


def test_root(engine: Fixture[str]) -> None:
    assert engine == "engine", (
        "the process fixture is anchored at this test's own package, so a "
        "failure here means the implied rootdir package moved"
    )
