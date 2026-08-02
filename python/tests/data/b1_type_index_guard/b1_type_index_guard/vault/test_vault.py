"""Positive control: the anchor package's own injection resolves.

Without it, the collection error this project exists to pin could just as well
mean ``LedgerHandle`` has no registered fixture at all — in which case the
refusal would be about absence rather than about the parameter's *name*.
"""

from __future__ import annotations

from oxitest import Fixture

from b1_type_index_guard._kinds import LedgerHandle


def test_the_anchor_package_injects_its_own_ledger(
    vault_ledger: Fixture[LedgerHandle],
) -> None:
    assert vault_ledger.label == "vault-ledger", (
        "the type has a registered, injectable match; if this fails, the "
        "collection refusal in audit/ is about a missing fixture rather than "
        "about the name-based validator this project pins"
    )
