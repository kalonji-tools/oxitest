"""Declares an inline fixture that only this file may use."""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


@oxi.fixture(lifetime="module")
def owned() -> int:
    return 7


def test_the_owner_can_use_it(fx: Fixtures) -> None:
    value = fx.test_owner.owned
    assert value == 7, f"the declaring file resolves its own fixture; got {value}"
