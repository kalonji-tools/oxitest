"""Smoke test that the importer exposes a fixture-registration hook.

Real end-to-end coverage lives in the acceptance suite (Task 14). This
test only asserts the shape of the bridge-to-Python entry point so a
regression on the hook name trips CI fast.
"""

from __future__ import annotations

from oxitest._bridge import importer


def test_importer_exposes_module_source_registration_hook() -> None:
    """Hook exists and is callable so the Rust bridge can call it by name."""
    hook = getattr(importer, "register_module_source_fixtures_for_module", None)
    assert callable(hook), (
        "importer must expose a hook the Rust bridge can call with the "
        "sibling __fixtures__.py path to register its @oxi.fixture-decorated "
        "functions into the FixtureRegistry"
    )
