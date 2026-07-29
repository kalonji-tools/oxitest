"""An inline fixture declaring lifetime="package" — illegal.

An inline fixture is anchored to its own module, so a lifetime wider than the
module would outlive the only scope that can see it. ADR-0009 Rule 4.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="package")
def engine() -> str:
    return "unreachable — collection must fail before this runs"


def test_uses_it() -> None:
    assert True, "collection must fail before this test executes"
