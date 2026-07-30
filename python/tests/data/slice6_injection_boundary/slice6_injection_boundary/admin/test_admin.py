"""The `Fixture[T]` route across the B1 boundary, plus the same-type probe.

Route 2 of B1 enforcement. Unlike ``fx.<ns>.<name>``, a ``Fixture[T]``
parameter carries no namespace segment, so the verdict is
``FixtureNotFoundError`` rather than ``BoundaryError`` — there is nothing to
attribute the anchor to. That is a decision, not an oversight; the acceptance
test pins the absence of the ``fixture-boundary`` code to keep the two routes'
contract visible.
"""

from __future__ import annotations

from oxitest import Fixture

from slice6_injection_boundary._kinds import ApiConnection, LedgerHandle


def test_sibling_package_injection_is_refused(
    api_conn: Fixture[ApiConnection],
) -> None:
    """Expected to ERROR — the fixture is anchored at the sibling ``api/``."""
    assert api_conn.label == "api", (
        "unreachable — resolution errors before the body runs; if this line "
        "ever executes, B1 let a sibling package's fixture through the "
        "Fixture[T] route"
    )


def test_a_visible_fixture_is_not_lost_to_an_invisible_same_typed_one(
    admin_ledger: Fixture[LedgerHandle],
) -> None:
    """Expected to PASS — the type index is unfiltered, the name step is not.

    ``FixtureRegistry.resolve`` picks from ``_by_type``, where ``api_ledger``
    sits alongside ``admin_ledger`` with no visibility filtering applied. If
    the invisible one won that lookup, this legal injection would fail and B1
    would be refusing something the test is entitled to.
    """
    assert admin_ledger.label == "admin-ledger", (
        "admin/ declares its own LedgerHandle fixture, so the injection is "
        "legal; a failure here means type-based resolution picked the "
        "api/-anchored rival and B1 then rejected it"
    )


def test_an_invisible_fixture_is_not_swapped_for_a_visible_same_typed_one(
    api_ledger: Fixture[LedgerHandle],
) -> None:
    """Expected to ERROR — the other direction of the same-type pair.

    ``admin_ledger`` is visible and shares the type, so a resolver that fell
    back to "any fixture of this type I can see" would quietly hand this test
    a substitute instead of refusing the access it actually wrote.
    """
    assert api_ledger.label == "api-ledger", (
        "unreachable — resolution errors before the body runs; reaching it "
        "means the run substituted a same-typed fixture for the one B1 refused"
    )
