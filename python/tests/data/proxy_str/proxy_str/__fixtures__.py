from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="module")
def price() -> float:
    return 3.14159
