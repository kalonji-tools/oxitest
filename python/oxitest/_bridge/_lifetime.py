from __future__ import annotations

__all__ = ["Lifetime"]

from enum import StrEnum, auto


class Lifetime(StrEnum):
    """Fixture lifetime tier per ADR-0009 Rule 2.

    All four values are declared so the union type stays stable across the
    redesign; ``_fixture_registry.LIFETIME_SCOPES`` is what says which ones
    actually work yet.
    """

    FUNCTION = auto()
    MODULE = auto()
    PACKAGE = auto()
    SESSION = auto()
