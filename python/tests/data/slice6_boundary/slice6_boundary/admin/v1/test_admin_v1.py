"""The other half of the duplicate-basename pair."""

from __future__ import annotations

from oxitest import Fixtures


def test_own_v1_namespace_wins(fx: Fixtures) -> None:
    assert fx.v1.thing == "admin-v1", (
        "resolving 'v1' from admin/ must not leak api/v1's value — the two "
        "share a namespace name and are told apart only by their anchors"
    )
