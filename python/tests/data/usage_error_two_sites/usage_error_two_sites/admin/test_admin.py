"""Site 1: a B1 boundary violation."""

from __future__ import annotations

from oxitest import Fixtures


def test_crosses_the_boundary(fx: Fixtures) -> None:
    assert fx.api.api_conn, "reaching a sibling package's fixture must be refused"
