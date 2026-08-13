"""One fixture, reached by two spellings of one union annotation."""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def thing() -> str:
    return "from-thing"
