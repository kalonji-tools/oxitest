"""Nearest-ancestor-wins, from the descendant that can see both declarations."""

from __future__ import annotations

from oxitest import Fixtures


def test_the_shortcut_picks_the_nearest_visible_declaration(fx: Fixtures) -> None:
    """Both `tx` declarations are visible here; the nearer one must win."""
    # Act
    value = fx.tx

    # Assert
    assert value.label == "v1", (
        "with `tx` declared at both api/ and api/v1/, the shortcut must take "
        "the deepest visible anchor; taking api's would mean the shortcut "
        "ignores locality and a descendant could not override a fixture"
    )


def test_the_ancestor_declaration_is_still_reachable_when_qualified(
    fx: Fixtures,
) -> None:
    """Shadowing by depth is not deletion — the outer `tx` keeps its own path."""
    # Act
    value = fx.api.tx

    # Assert
    assert value.label == "api", (
        "qualifying by the ancestor's namespace must reach the ancestor's "
        "declaration even when a nearer one shadows it in shortcut form; "
        "otherwise nearest-wins would make the outer fixture unaddressable"
    )
