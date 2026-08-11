"""The two halves of the advice the frozen-fixture hint gives (#2036)."""

from __future__ import annotations

from fixture_freeze_boundary.__fixtures__ import PerTestBox, WiderBox
from oxitest import Fixture, SharedFixtureMutationError, raises


def test_function_lifetime_value_is_mutable(per_test: Fixture[PerTestBox]) -> None:
    """The hint sends the user to `lifetime="function"` for a mutable copy."""
    # Act
    per_test.value = 1

    # Assert
    assert per_test.value == 1, (
        "a function-lifetime value dies with the test, so it is cached raw and "
        "stays mutable — if this fails the hint sends the user to a tier that "
        "does not give them what it promises"
    )


def test_wider_lifetime_value_is_frozen(wider: Fixture[WiderBox]) -> None:
    """The condition that makes the hint fire at all."""
    # Act / Assert
    with raises(SharedFixtureMutationError):
        wider.value = 1
