"""Boundary-crossing sum type for parametrize discriminator (ADR-0007 Rule 2).

Replaces `param_id: str | None` as the co-varying-Optional discriminator across
TestMeta, CollectedItem, WorkerTaskItem, TestContext, and the executor entry
point. Wire format is unchanged: `to_wire()` returns `str | None` (the shape
the Rust bridge already produces via `WorkerTaskItem.param_id`).
"""

from __future__ import annotations

__all__ = ["Parametrized", "Solitary", "TestKind", "from_wire"]

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Parametrized:
    """One case of a parametrized test, discriminated by its case id."""

    param_id: str

    def to_wire(self) -> str | None:
        return self.param_id


@dataclass(frozen=True, slots=True)
class Solitary:
    """A non-parametrized test — no case id."""

    def to_wire(self) -> str | None:
        return None


TestKind = Parametrized | Solitary


def from_wire(param_id: str | None) -> TestKind:
    """Reconstruct the sum type from the wire-shape Optional."""
    return Parametrized(param_id=param_id) if param_id is not None else Solitary()
