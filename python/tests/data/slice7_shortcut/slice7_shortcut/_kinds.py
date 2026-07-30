"""Binding types for this project's fixtures.

A plain module, not a declaration file: declaration files are loaded under a
synthesised module name, so importing one by its package path from a test would
execute it a second time and hand back a *different* class object. One class
object per type is what keeps ``Fixture[T]``'s type route pointing where these
tests think it points.
"""

from __future__ import annotations


class Tx:
    """Return type of the fixtures the nearest-ancestor tests compare."""

    def __init__(self, label: str) -> None:
        self.label = label


class Shadowed:
    """Return type of the fixture whose name collides with a package segment."""

    def __init__(self, label: str) -> None:
        self.label = label
