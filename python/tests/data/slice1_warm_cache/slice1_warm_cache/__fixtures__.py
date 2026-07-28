from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def widget() -> object:
    return object()
