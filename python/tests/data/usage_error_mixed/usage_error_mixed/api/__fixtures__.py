"""One fixture anchored to api/, so admin/ cannot see it."""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def api_conn() -> str:
    return "anchored to api/"
