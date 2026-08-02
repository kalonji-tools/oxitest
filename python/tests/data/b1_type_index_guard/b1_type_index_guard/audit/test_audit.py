"""A ``Fixture[T]`` whose *name* matches nothing and whose *type* matches one.

``pool`` is registered nowhere. ``LedgerHandle`` is registered exactly once, in
``vault/`` — a package this test cannot see under B1. Today the run never gets
far enough to find that out: ``FixtureValidator.validate_fixture_names`` rejects
the parameter at collection time on its name alone.

That rejection is the whole of #1768. Remove it and ``resolve_param``'s
type-first lookup starts getting the final say, reading a ``_by_type`` index
that has no visibility filtering at all.
"""

from __future__ import annotations

from oxitest import Fixture

from b1_type_index_guard._kinds import LedgerHandle


def test_a_name_that_matches_nothing_is_refused_at_collection(
    pool: Fixture[LedgerHandle],
) -> None:
    """Expected never to run — collection refuses the parameter first."""
    assert pool.label == "vault-ledger", (
        "unreachable — collection fails before any test runs; reaching this "
        "line means a parameter resolved purely by type across a B1 boundary"
    )
