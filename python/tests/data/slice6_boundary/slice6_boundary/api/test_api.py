"""The base case and the ancestor case, from the anchor package itself."""

from __future__ import annotations

from oxitest import Fixtures


def test_own_package_fixture(fx: Fixtures) -> None:
    assert fx.api.api_conn == "api", (
        "a test in the anchor package itself must resolve its own fixture — "
        "the base case that stops the filter from banning everything"
    )


def test_ancestor_package_fixture(fx: Fixtures) -> None:
    assert fx.slice6_boundary.root_conn == "root", (
        "B1 is an ancestor chain, not directory equality: a fixture anchored "
        "further up must stay reachable, or every project-wide fixture becomes "
        "unusable the moment a test moves into a subdirectory"
    )
