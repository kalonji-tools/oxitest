"""Positive control.

Without this, a project where ``api_only`` never registered at all would
satisfy the admin-side boundary assertion for entirely the wrong reason.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_the_anchor_package_reaches_its_own_fixture_by_shortcut(
    fx: Fixtures,
) -> None:
    # Act
    value = fx.api_only

    # Assert
    assert value.label == "api", (
        "the shortcut must work from inside the anchor package; if it did not, "
        "the sibling's failure would prove nothing about B1"
    )
