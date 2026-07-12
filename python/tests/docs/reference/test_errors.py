"""Tested examples for the errors reference page."""

from dataclasses import dataclass

import oxitest


# fmt: off
# --8<-- [start:dataclass-parametrize-fix]
@dataclass(frozen=True)
class AddCase:
    a: int
    b: int
    expected: int


@oxitest.parametrize(
    basic=AddCase(a=1, b=2, expected=3),
    zero=AddCase(a=0, b=0, expected=0),
)
def test_add(a: int, b: int, expected: int) -> None:
    assert a + b == expected, f"{a} + {b} should equal {expected}"
# --8<-- [end:dataclass-parametrize-fix]
# fmt: on
