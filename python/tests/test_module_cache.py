# python/tests/test_module_cache.py
"""Integration test: module-level code runs exactly once per session.

With a broken module cache, each test re-executes the module, so the
process-level counter increments once per test. With the cache working,
both tests should see count == 1.

NOTE: This file uses a sys.modules sentinel to count how many times
the module body executes. The sentinel is scoped to the oxitest session
that runs this file — each session gets a fresh import. If this file
is imported by another test runner in the same process, the counter
may be stale. This is intentional: the test validates oxitest's own
module cache, which guarantees one import per file per session.
"""

from __future__ import annotations

import sys

import oxitest

oxi_mark = oxitest.mark.inprocess

# Use sys.modules as process-persistent storage so the counter survives
# module eviction between tests.  The key is unique enough to avoid
# collision with other test files.
_KEY = "_oxitest_load_counter_test_module_cache"
if _KEY not in sys.modules:
    counter = type(sys)(_KEY)
    setattr(counter, "n", 0)  # noqa: B010 — dynamic module-namespace attr
    sys.modules[_KEY] = counter
_counter = sys.modules[_KEY]
_counter.n += 1


def test_module_loaded_once_first_test() -> None:
    """Module should have been loaded exactly once by the time this test runs."""
    assert _counter.n == 1, (
        f"module loaded {_counter.n} time(s) before first test, expected 1"
    )


def test_module_loaded_once_second_test() -> None:
    """Module should not have been reloaded between tests."""
    assert _counter.n == 1, (
        f"module loaded {_counter.n} time(s) before second test, expected 1"
    )
