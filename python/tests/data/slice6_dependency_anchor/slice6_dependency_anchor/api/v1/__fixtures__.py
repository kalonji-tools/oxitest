"""The dependency ``leaky`` must not be able to reach — anchored below it."""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def thing() -> str:
    return "v1"
