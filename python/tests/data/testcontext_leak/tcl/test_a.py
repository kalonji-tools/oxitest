"""Three tests: one keeps the process fixture alive, two die in resolution.

The two failing tests take different exits out of ``run_test``. One returns a
``TestResult``, the other raises. A caller selects the pair it wants with
``-E``: one run holding both failing tests would let the probe report only the
last one, and neither exit would stay pinned.
"""

from __future__ import annotations

from oxitest import Fixture, TestIdentity


def test_uses_probe(probe: Fixture[str]) -> None:
    """Keeps the process-lifetime fixture alive so its teardown runs last."""
    assert probe, "the process fixture must be injected"


def test_z_last_fails_in_resolution(boom: Fixture[str]) -> None:
    """Never runs — the fixture raises during setup, taking the early return."""
    assert boom, "unreachable"


def test_z_last_raises_in_resolution(ident: TestIdentity) -> None:
    """Never runs — TestIdentity in a test signature raises a UsageError.

    No ``except`` in ``_load_and_resolve`` names that class, so this exit is
    the raise rather than the early return above.
    """
    assert ident is not None, "unreachable"
