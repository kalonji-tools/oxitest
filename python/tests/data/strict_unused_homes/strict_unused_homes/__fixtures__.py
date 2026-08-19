"""An unused declaration in `__fixtures__.py`, the first of ADR-0009 Rule 5's homes.

Nothing references this fixture. The unused-fixture check must name it. Before
#2200 the check filtered on a declaration path ending in `conftest.py`, which
#1720 made unsatisfiable, so no home reported anything.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def never_used_in_fixtures_file() -> str:
    return "unused"
