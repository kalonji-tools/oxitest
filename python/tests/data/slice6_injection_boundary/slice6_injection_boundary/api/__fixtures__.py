"""Fixtures anchored at ``api/``. Nothing outside ``api/`` may reach them.

``api_ledger`` exists only to give ``admin/__fixtures__.py``'s ``admin_ledger``
a same-typed rival that the admin tests cannot see.
"""

from __future__ import annotations

import oxitest as oxi

from slice6_injection_boundary._kinds import ApiConnection, LedgerHandle


@oxi.fixture(lifetime="function")
def api_conn() -> ApiConnection:
    return ApiConnection("api")


@oxi.fixture(lifetime="function")
def api_ledger() -> LedgerHandle:
    return LedgerHandle("api-ledger")
