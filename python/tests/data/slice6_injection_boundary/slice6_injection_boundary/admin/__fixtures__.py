"""A fixture the admin tests *can* see, sharing a type with one they cannot.

``api/__fixtures__.py`` declares ``api_ledger`` with the same ``LedgerHandle``
return type. Both land in the registry's ``_by_type`` bucket, which does no
visibility filtering, so this pair is what proves a legal injection is not
lost to an invisible same-typed rival.
"""

from __future__ import annotations

import oxitest as oxi

from slice6_injection_boundary._kinds import LedgerHandle


@oxi.fixture(lifetime="function")
def admin_ledger() -> LedgerHandle:
    return LedgerHandle("admin-ledger")
