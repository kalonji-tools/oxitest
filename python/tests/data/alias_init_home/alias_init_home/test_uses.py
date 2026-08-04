"""Consume the aliased `__init__.py` declaration."""

from __future__ import annotations

from oxitest import Fixtures


def test_alias_in_init_resolves(fx: Fixtures) -> None:
    value = fx.alias_init_home.shared_db
    assert value == "db", (
        f"an aliased declaration in __init__.py must register; this row failed "
        f"silently before #1859 because reserved=false suppressed the hint; "
        f"got {value!r}"
    )
