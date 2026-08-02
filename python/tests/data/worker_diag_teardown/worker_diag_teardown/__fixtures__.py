"""A module-lifetime fixture whose teardown always fails.

Exists to force the one event #1840 is about: a diagnostic emitted after a
task group's final result line. Six modules means six teardowns, which is what
makes the per-worker loss countable rather than anecdotal.
"""

from __future__ import annotations

import oxitest as oxi
from oxitest import Yields

#: The failure text ``test_worker_diagnostics.py`` counts in the run output.
#: Kept as a constant so the marker has exactly one definition.
EXPLOSION = "TEARDOWN EXPLODED"


@oxi.fixture(lifetime="module")
def exploding() -> Yields[str]:
    yield "value"
    raise RuntimeError(EXPLOSION)
