from __future__ import annotations

from oxitest import Fixture


def test_b(engine: Fixture[str]) -> None:
    assert engine == "engine", (
        "the second declared tree must reach the same root-anchored fixture; if "
        "only one tree resolves it, the root is not covering both declarations"
    )
