"""A test at the tree root, so nested/ is genuinely below it.

Slice 4's lesson: without a root-level module the collected tree bottoms out at
nested/, which then IS the rootdir package.
"""

from __future__ import annotations


def test_placeholder_at_root() -> None:
    assert True, "the run must fail at collection, before this test executes"
