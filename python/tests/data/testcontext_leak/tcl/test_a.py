"""Two tests: one keeps the process fixture alive, one fails in resolution."""

from __future__ import annotations

from oxitest import Fixture


def test_uses_probe(probe: Fixture[str]) -> None:
    """Keeps the process-lifetime fixture alive so its teardown runs last."""
    assert probe, "the process fixture must be injected"


def test_z_last_fails_in_resolution(boom: Fixture[str]) -> None:
    """Never runs — the fixture raises during setup, taking the early return."""
    assert boom, "unreachable"
