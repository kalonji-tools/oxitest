"""The test's own position is legal; the fixture's dependency is not.

``api/v1`` is a descendant of ``api``, so reaching ``fx.api.leaky`` from here is
allowed. What must fail is one step further in: ``leaky`` asking for ``thing``.
Before the boundary descent was added, the chain carried the *test's* module
path and this resolved cleanly.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_a_legal_access_to_an_illegal_fixture(fx: Fixtures) -> None:
    """Expected to ERROR — on `leaky`'s dependency, not on the access itself."""
    _ = fx.api.leaky
