"""The nearer ``tx``.

Anchored at ``api/v1/``, so it shadows ``api``'s for tests in this subtree
only — ``api/test_api.py`` still sees its own.
"""

from __future__ import annotations

import oxitest as oxi

from slice7_shortcut._kinds import Tx


@oxi.fixture(lifetime="function")
def tx() -> Tx:
    return Tx("v1")
