"""Binding type for the cross-boundary probe.

A plain module — see the sibling project's ``_kinds`` for why a declaration
file would not do.
"""

from __future__ import annotations


class ApiOnly:
    """Return type of a fixture anchored where the admin tests cannot see it."""

    def __init__(self, label: str) -> None:
        self.label = label
