"""A legal declaration in the second home of the same directory.

Present so that both homes register. With only one registering the anchor is not
actually shared and the defect this project exists for cannot reproduce.
"""

from __future__ import annotations

import oxitest as ox


@ox.fixture(lifetime="function")
def other() -> str:
    return "other"
