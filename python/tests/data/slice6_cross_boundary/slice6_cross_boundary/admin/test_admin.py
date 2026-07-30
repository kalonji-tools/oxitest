"""Two sibling-package violations: one spelled right, one spelled wrong."""

from __future__ import annotations

from oxitest import Fixtures


def test_sibling_package_access_is_refused(fx: Fixtures) -> None:
    """Expected to ERROR — a BoundaryError on a leaf that really exists."""
    _ = fx.api.api_conn


def test_sibling_package_typo_is_refused_as_a_boundary(fx: Fixtures) -> None:
    """Expected to ERROR — still a BoundaryError, with the leaf fact appended."""
    _ = fx.api.typo
