from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def conn() -> object:
    return object()
