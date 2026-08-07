"""A plain helper reached by import. Nothing injects into it — that is the point."""

from __future__ import annotations

from oxitest import TestContext


def whoami() -> str:
    """Return the running test's node id, with no ``ctx`` parameter in sight."""
    return TestContext.current().node_id
