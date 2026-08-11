"""Two lifetimes either side of the freeze boundary.

``function`` values are cached raw; every wider tier is wrapped in
``FrozenProxy``. The two declarations return different types so resolution is
unambiguous by binding type alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import oxitest as oxi


@dataclass
class PerTestBox:
    """A value at the mutable side of the boundary."""

    value: int = 0


@dataclass
class WiderBox:
    """A value at the frozen side of the boundary."""

    value: int = 0


@oxi.fixture(lifetime="function")
def per_test() -> PerTestBox:
    return PerTestBox()


@oxi.fixture(lifetime="module")
def wider() -> WiderBox:
    return WiderBox()
