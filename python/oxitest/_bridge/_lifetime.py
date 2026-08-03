from __future__ import annotations

__all__ = ["Lifetime"]

from enum import StrEnum, auto


class Lifetime(StrEnum):
    """Fixture lifetime tier per ADR-0009 Rule 2.

    All four values are declared so the union type stays stable across the
    redesign; ``_fixture_registry.LIFETIME_SCOPES`` is what says which ones
    actually work yet.

    ``PROCESS`` was spelled ``SESSION`` until #1777. The rename is the point of
    that change rather than cosmetic: the tier promised a per-process resource
    and delivered one per *task group*, so "session" named a boundary the
    implementation never had. It is also the only tier whose instance count the
    user sets with ``-n`` rather than with their directory layout — the other
    three name code-structural units.
    """

    FUNCTION = auto()
    MODULE = auto()
    PACKAGE = auto()
    PROCESS = auto()
