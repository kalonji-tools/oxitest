"""A process declaration in the rootdir package of a disjoint declaration.

`tests/` and `docs/` share no ancestor below the project root, so the root
*is* the rootdir package and this file is the one legal site for
``lifetime="process"``. Moving this file into either declared tree must make
the run fail.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="process")
def engine() -> str:
    return "engine"
