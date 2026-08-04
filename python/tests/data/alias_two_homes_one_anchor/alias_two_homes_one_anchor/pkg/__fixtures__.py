"""The file that actually declares the illegal tier.

Its sibling `__init__.py` declares a legal one. Both register under the *same*
anchor — the directory — so a query keyed on the anchor hands each of them the
other's declarations.
"""

from __future__ import annotations

import oxitest as ox


@ox.fixture(lifetime="process")
def engine() -> str:
    return "unreachable — collection must fail before this runs"
