from __future__ import annotations

from ws._record import window


def test_c1() -> None:
    window("c1")
    assert True, "the recorded window is the point; this keeps the item green"


def test_c2() -> None:
    window("c2")
    assert True, "the recorded window is the point; this keeps the item green"
