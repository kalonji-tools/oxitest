from __future__ import annotations

from oxitest import Fixture


def test_x(engine: Fixture[str]) -> None:
    assert engine == "engine", (
        "testpaths = ['.'] resolves to rootdir/'.', and Rule 4 compares the "
        "rootdir package to an anchor by equality — this pins that the two "
        "still match"
    )
