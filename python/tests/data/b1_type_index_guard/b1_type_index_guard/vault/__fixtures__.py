"""The only ``LedgerHandle`` in the run, anchored where ``audit/`` cannot see it.

Exactly one is the point. ``FixtureRegistry.resolve`` short-circuits on a single
candidate without ever consulting the qualifier, so a lone entry is the case
where the unfiltered ``_by_type`` index is closest to having the final say.
"""

from __future__ import annotations

import oxitest as oxi

from b1_type_index_guard._kinds import LedgerHandle


@oxi.fixture(lifetime="function")
def vault_ledger() -> LedgerHandle:
    return LedgerHandle("vault-ledger")
