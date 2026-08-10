"""Site 2's fixtures: a wider-lived async fixture over a shorter-lived one."""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixture


@oxi.fixture(lifetime="function")
async def short_lived() -> str:
    return "bound to one test's event loop"


@oxi.fixture(lifetime="module")
async def long_lived(short_lived: Fixture[str]) -> str:
    return f"outlives the test that built {short_lived}"
