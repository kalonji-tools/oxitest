"""Consumer; must never execute."""

from __future__ import annotations


def test_x() -> None:
    assert True, "collection must fail before this test executes"
