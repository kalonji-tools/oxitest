"""Namespace ``v1`` again — same basename, disjoint subtree from ``api/v1``.

An anchor-blind duplicate check kills the whole run here, so the mere fact that
this project collects at all is part of what the acceptance test proves.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def thing() -> str:
    return "admin-v1"
