"""A test at the tree root, so pkg/ is genuinely below it.

Slice 4's lesson: without a root-level module the collected tree bottoms out at
pkg/, which would then BE the rootdir package and make the declaration legal.
"""

from __future__ import annotations


def test_placeholder_at_root() -> None:
    assert True, "the run must fail at collection, before this test executes"
