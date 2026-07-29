"""A test at the root of the tree, so ``nested/`` is genuinely below it.

Without this file the collected tree would bottom out at ``nested/``, making
``nested/`` itself the rootdir package — and the session declaration there
would be legal, so the rejection under test would never fire.
"""

from __future__ import annotations


def test_placeholder_at_root() -> None:
    assert True, "the run must fail at collection, before this test executes"
