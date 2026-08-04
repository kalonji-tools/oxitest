"""A root-level test, so pkg/ is genuinely below the tree root."""

from __future__ import annotations


def test_placeholder_at_root() -> None:
    assert True, "the run must fail at collection, before this executes"
