"""Binding type for this project's single fixture.

A class this project owns, for the same reason ``slice6_injection_boundary``
owns its own: ``Fixture[T]`` resolves by *type* first, and a builtin like
``str`` would share a ``_by_type`` bucket with anything else in the run that
happens to return one.

It lives in a plain module rather than in ``vault/__fixtures__.py`` because a
declaration file is loaded under a synthesised module name, so importing it a
second time by package path from a sibling test yields a *different* class
object — the type lookup would then miss and the probe would measure the name
route instead of the type route.
"""

from __future__ import annotations


class LedgerHandle:
    """Return type of the only fixture in this project."""

    def __init__(self, label: str) -> None:
        self.label = label
