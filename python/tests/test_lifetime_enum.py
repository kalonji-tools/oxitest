"""Tests for the Lifetime StrEnum."""

from __future__ import annotations

from oxitest._bridge._lifetime import Lifetime


def test_lifetime_values_are_lowercased_names() -> None:
    """Lifetime members should have lowercased values via auto()."""
    assert Lifetime.FUNCTION == "function", (
        "auto() under StrEnum should yield the lowercased member name"
    )
    assert Lifetime.MODULE == "module", "MODULE should have value 'module'"
    assert Lifetime.PACKAGE == "package", "PACKAGE should have value 'package'"
    assert Lifetime.SESSION == "session", "SESSION should have value 'session'"


def test_lifetime_declares_all_four_tiers() -> None:
    """All four ADR-0009 Rule 2 tiers are pre-declared."""
    tiers = [t.value for t in Lifetime]
    assert tiers == ["function", "module", "package", "session"], (
        "all four ADR-0009 Rule 2 tiers must be declared even though slice 1 "
        "only implements FUNCTION — future slices add behavior, not members"
    )


def test_lifetime_string_lookup() -> None:
    """StrEnum lookup by string value must round-trip."""
    assert Lifetime("function") is Lifetime.FUNCTION, (
        "StrEnum lookup by string value must round-trip"
    )
