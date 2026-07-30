"""Binding type for the sync-test-reaches-async-fixture probe."""

from __future__ import annotations


class Conn:
    """Return type of the async fixture a sync test must not receive."""

    def __init__(self, label: str) -> None:
        self.label = label
