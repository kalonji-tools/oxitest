"""The only fixture in this project — anchored at ``api/``.

Nothing outside ``api/`` may reach it. ``api/test_api.py`` proves it registered
at all, which is what keeps the three violation tests honest.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def api_conn() -> str:
    return "api"
