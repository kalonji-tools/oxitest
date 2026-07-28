from __future__ import annotations

__all__ = ["Lifetime"]

from enum import StrEnum, auto


class Lifetime(StrEnum):
    """Fixture lifetime tier per ADR-0009 Rule 2.

    Slice 1 exposes only FUNCTION. MODULE / PACKAGE / SESSION arrive in
    slices 2 / 3 / 4 — enum values are pre-declared so the union type is
    stable across the redesign.
    """

    FUNCTION = auto()
    MODULE = auto()
    PACKAGE = auto()
    SESSION = auto()
