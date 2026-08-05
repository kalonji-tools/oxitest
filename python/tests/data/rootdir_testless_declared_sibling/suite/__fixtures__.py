"""A process declaration in the only declared directory that holds tests.

`suite/` is the rootdir package precisely because `srconly/` — declared, but
holding no test file — does not count toward the fold. Fold both and the root
becomes the project root, one level up, and this declaration is rejected.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="process")
def engine() -> str:
    return "engine"
