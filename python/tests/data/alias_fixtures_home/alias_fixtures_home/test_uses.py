"""Consume the aliased declaration through the namespace proxy."""

from __future__ import annotations

from oxitest import Fixtures


def test_alias_declared_fixture_resolves(fx: Fixtures) -> None:
    value = fx.alias_fixtures_home.conn
    assert value == "connected", (
        f"an aliased @ox.fixture registers at runtime by marker attribute; if it "
        f"does not resolve, prescan gated the import away and the declaration "
        f"was silently dropped (#1859); got {value!r}"
    )
