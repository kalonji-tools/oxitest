from __future__ import annotations

from oxitest import Fixture


def test_nested(engine: Fixture[str]) -> None:
    assert engine == "engine", (
        "a descendant of the anchor package must see the fixture (ADR-0009 B1); "
        "narrowing the run to this directory must not change that"
    )
