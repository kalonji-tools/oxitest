# python/tests/test_module_cache.py
"""Integration test: module-level code runs exactly once per session.

With a broken module cache, each test re-executes the module, so the
process-level counter increments once per test. With the cache working,
both tests should see count == 1.
"""

from __future__ import annotations

import sys

# Use sys.modules as process-persistent storage so the counter survives
# module eviction between tests.
_KEY = "_oxitest_load_counter"
if _KEY not in sys.modules:
    counter = type(sys)(_KEY)
    counter.n = 0  # type: ignore[attr-defined] # ty: ignore[unresolved-attribute]
    sys.modules[_KEY] = counter
_counter = sys.modules[_KEY]
_counter.n += 1  # type: ignore[attr-defined]


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
