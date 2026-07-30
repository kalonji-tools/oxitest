"""Rootdir-package fixture — the ancestor every test in this project can reach.

Present so the acceptance tests can tell a correct B1 filter from one that
simply refuses everything outside the test's own directory. Reaching this from
``api/`` must keep working.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="function")
def root_conn() -> str:
    return "root"
