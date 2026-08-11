"""The positive control: the anchor package resolves its own fixture.

Without it, a tree where `api_conn` never registered would produce the same
refusals next door for entirely the wrong reason.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_the_anchor_package_resolves_its_own_fixture(fx: Fixtures) -> None:
    assert fx.api.api_conn == "api", (
        "the anchor package must reach its own fixture, or the sibling's "
        "refusals are about absence rather than about the boundary"
    )
