"""A package-lifetime fixture in a package that also marks a test inprocess.

The combination cannot be honoured: inprocess tests run on the coordinator's
session while the rest of the package runs on a worker's, so the fixture would
be built once in each. Collection must reject it rather than let the
exactly-once guarantee quietly fail under ``-n``.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="package")
def engine() -> str:
    return "engine"
