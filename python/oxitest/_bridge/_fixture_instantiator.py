"""Fixture resolution and instantiation — extracted from FixtureSession."""

from __future__ import annotations

__all__ = ["ScopeRefs"]

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScopeRefs:
    """References to the scope a fixture should be cached/torn down in."""

    cache: dict[str, Any]
    teardowns: list[Callable[[], None]]
    hits: dict[str, int]
    misses: dict[str, int]
