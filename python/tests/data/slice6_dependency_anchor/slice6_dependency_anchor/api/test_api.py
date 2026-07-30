"""Positive control for the ``api`` namespace, and the anchor's own registration."""

from __future__ import annotations

from oxitest import Fixtures


def test_the_anchor_package_resolves_a_dependency_free_fixture(fx: Fixtures) -> None:
    assert fx.api.sane == "sane", (
        "api/ must be a registered declaration home — otherwise 'leaky' is "
        "simply unknown and the dependency-anchor failure proves nothing"
    )
