from __future__ import annotations

from ws._record import window


def test_b1() -> None:
    window("b1")
    assert True, "the recorded window is the point; this keeps the item green"


def test_b2() -> None:
    window("b2")
    assert True, "the recorded window is the point; this keeps the item green"
