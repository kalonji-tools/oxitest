"""The negative control: a segment that is declared nowhere in the run.

This project declares no fixtures at all, so ``nope`` cannot be an unreachable
anchor — it is a typo, and must be reported as one.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_unknown_namespace_is_a_typo_not_a_boundary(fx: Fixtures) -> None:
    """Expected to ERROR — as not-found, never as a boundary violation."""
    _ = fx.nope.x
