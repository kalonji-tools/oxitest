"""An aliased lifetime="package" declaration over a two-module subtree.

The tier's exactly-once guarantee is structural: the scheduler co-locates the
subtree onto one worker. That only happens if the declaration reaches the
scheduler, which before #1859 it did not for this spelling.
"""

from __future__ import annotations

import oxitest as ox


@ox.fixture(lifetime="package")
def engine() -> str:
    return "engine"
