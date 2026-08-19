"""An unused declaration in `__init__.py`, the second declaration home.

A rename of the filter to `__fixtures__.py` would have skipped this home
silently, which is the narrowing #1722 recorded when it repaired the twin
detector in `src/inspect/signals.rs`.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def never_used_in_init() -> int:
    return 1
