"""Declares an inline fixture and uses it legally, from its own module."""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixtures


@oxi.fixture(lifetime="module")
def inline_only() -> str:
    return "inline"


def test_its_own_module_may_use_it(fx: Fixtures) -> None:
    # Act
    value = fx.inline_only

    # Assert
    assert value == "inline", (
        "an inline fixture must be reachable by shortcut from the module that "
        "declares it, or the sibling's failure below proves nothing"
    )
