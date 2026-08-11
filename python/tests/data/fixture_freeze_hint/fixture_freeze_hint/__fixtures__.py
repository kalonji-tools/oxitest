"""One wide-lifetime fixture, so a write to it is refused."""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi


@dataclass
class WiderBox:
    """A value that outlives the test, so its proxy refuses writes."""

    value: int = 0


@oxi.fixture(lifetime="module")
def wider() -> WiderBox:
    return WiderBox()
