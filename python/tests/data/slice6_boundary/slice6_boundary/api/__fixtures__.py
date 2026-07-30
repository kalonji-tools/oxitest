"""Fixture anchored at ``api/`` — reachable from ``api/`` and everything below.

Function lifetime on purpose: this slice is about visibility only, and the
wider tiers pull in rootdir semantics (#1755) that would confuse the verdict.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def api_conn() -> str:
    return "api"
