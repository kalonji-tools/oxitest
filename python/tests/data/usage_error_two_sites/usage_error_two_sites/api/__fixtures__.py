"""Site 1's fixture: anchored to api/, invisible from admin/."""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def api_conn() -> str:
    return "anchored to api/"
