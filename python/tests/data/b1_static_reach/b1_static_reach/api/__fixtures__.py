"""The anchor package. Its fixture is legal here and nowhere above it."""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def api_conn() -> str:
    return "api"
