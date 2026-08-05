from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="process")
def engine() -> str:
    return "engine"
