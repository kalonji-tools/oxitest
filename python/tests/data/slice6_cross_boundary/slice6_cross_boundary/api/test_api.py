"""Positive control: the anchor package's own test must still pass."""

from __future__ import annotations

from oxitest import Fixtures


def test_the_anchor_package_still_resolves_its_own_fixture(fx: Fixtures) -> None:
    assert fx.api.api_conn == "api", (
        "without this passing, a project where api_conn never registered would "
        "produce the same three errors and every boundary assertion would hold "
        "for entirely the wrong reason"
    )
