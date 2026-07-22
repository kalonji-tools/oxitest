"""Round-trip tests for the TestKind sum type (ADR-0007 Rule 2 exemplar)."""

from __future__ import annotations

from oxitest._bridge._test_kind import (
    Parametrized,
    Solitary,
    TestKind,
    from_wire,
)


def test_parametrized_round_trips_through_wire() -> None:
    """Parametrized → to_wire() → from_wire() → Parametrized preserves param_id."""
    original: TestKind = Parametrized(param_id="x=2-y=3")

    wire = original.to_wire()
    restored = from_wire(wire)

    assert wire == "x=2-y=3", (
        "Parametrized.to_wire() must return the param_id string so the"
        " wire adapter can carry it across the worker JSON channel"
    )
    assert restored == original, (
        "from_wire(Parametrized.to_wire()) must return an equal Parametrized"
        " for the sum type to be truly boundary-crossing"
    )


def test_solitary_round_trips_through_wire() -> None:
    """Solitary → to_wire() → from_wire() → Solitary preserves the variant."""
    original: TestKind = Solitary()

    wire = original.to_wire()
    restored = from_wire(wire)

    assert wire is None, (
        "Solitary.to_wire() must return None so the wire adapter emits the"
        " absence-of-param_id shape the Rust bridge already accepts"
    )
    assert restored == original, (
        "from_wire(None) must yield Solitary(), not Parametrized with empty id,"
        " so the None discriminator round-trips faithfully"
    )


def test_from_wire_none_yields_solitary() -> None:
    """Direct from_wire(None) discriminator check (not via to_wire())."""
    result = from_wire(None)

    assert isinstance(result, Solitary), (
        "from_wire(None) must return Solitary — the sum type's whole point"
        " is that None at the wire boundary maps to a well-typed variant"
    )


def test_from_wire_str_yields_parametrized() -> None:
    """Direct from_wire('id') check."""
    result = from_wire("case_alpha")

    assert isinstance(result, Parametrized), (
        "from_wire(str) must return Parametrized so callers can pattern-match"
        " on the variant instead of reintroducing an is-None guard"
    )
    assert result.param_id == "case_alpha", (
        "Parametrized must carry the exact param_id string received from wire"
        " — the case id is the sole discriminator between parametrize cases"
    )


def test_from_wire_empty_string_yields_parametrized() -> None:
    """Pin: empty string is a valid Some("") wire value → Parametrized(param_id="").

    The discriminator is None-vs-not-None, not truthiness. Rust's wire type
    ``Option<&str>`` distinguishes ``None`` from ``Some("")``, and the sum type
    must preserve that distinction — mapping Some("") to Solitary would
    silently swallow malformed collection output.
    """
    result = from_wire("")

    assert isinstance(result, Parametrized), (
        "from_wire('') must return Parametrized, not Solitary — Rust's"
        " Option<&str> treats Some('') as present-but-empty, and collapsing"
        " it to Solitary would hide a real wire-format bug from downstream"
        " parametrize resolution"
    )
    assert result.param_id == "", (
        "Parametrized must carry the exact param_id received, including the"
        " empty string — resolve_parametrize downstream will raise a"
        " ParametrizeError with the empty id in the message, surfacing the"
        " boundary issue instead of masking it"
    )
