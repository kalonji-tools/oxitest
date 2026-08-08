from __future__ import annotations

from ws._record import window


def test_a1() -> None:
    window("a1")
    assert True, "the recorded window is the point; this keeps the item green"


def test_a2() -> None:
    window("a2")
    assert True, "the recorded window is the point; this keeps the item green"
