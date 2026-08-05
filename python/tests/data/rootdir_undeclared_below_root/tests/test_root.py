"""A test at the top of the tree, so ``nested/`` is genuinely below it.

Without this file the implied root would bottom out at ``nested/``, making
``nested/`` itself the rootdir package — the declaration there would be legal
and the rejection under test would never fire.
"""

from __future__ import annotations


def test_placeholder_at_root() -> None:
    assert True, "the run must fail at collection, before this test executes"
