from __future__ import annotations


def test_never_runs() -> None:
    msg = "if this test ran, the collision check missed"
    raise AssertionError(msg)
