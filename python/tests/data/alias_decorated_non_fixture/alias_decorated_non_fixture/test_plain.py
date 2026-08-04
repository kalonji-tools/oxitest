"""No fixtures anywhere — the project must simply collect and pass."""

from __future__ import annotations


def test_plain() -> None:
    assert True, (
        "a project whose __fixtures__.py declares nothing must still collect; "
        "the decorator there belongs to functools, not oxitest"
    )
