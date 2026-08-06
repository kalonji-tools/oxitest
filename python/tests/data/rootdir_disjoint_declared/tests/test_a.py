from __future__ import annotations

from oxitest import Fixture


def test_a(engine: Fixture[str]) -> None:
    assert engine == "engine", (
        "the process fixture is anchored at the project root, which is the "
        "rootdir package for a disjoint declaration; a failure here means the "
        "fold stopped landing on the root"
    )
