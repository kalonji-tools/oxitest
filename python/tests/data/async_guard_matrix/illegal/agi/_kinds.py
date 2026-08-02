"""Binding type for the FixtureRef probe."""

from __future__ import annotations


class Conn:
    """Value of a namespaced async fixture a sync test must not receive."""

    def __init__(self, label: str) -> None:
        self.label = label
