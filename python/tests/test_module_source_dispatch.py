"""Smoke test for ModuleSource dispatch (Task 7 of ADR-0009 slice 1).

Deep behavioral coverage lives in the end-to-end acceptance suite (Task 14).
Here we assert the module-symbol surface exists — a regression on the
import shape trips CI fast without needing full session bootstrap.
"""

from __future__ import annotations

from oxitest._bridge import _fixture_instantiator


def test_instantiator_module_imports() -> None:
    """The instantiator module must remain importable after Task 7 changes."""
    assert _fixture_instantiator is not None, (
        "instantiator module must be importable — Task 7 must not break "
        "the module's import surface"
    )


def test_fixture_registry_and_source_types_referenced_by_instantiator() -> None:
    """Task 7 adds ModuleSource to the instantiator's imports.

    Verify the module's __all__ / module-level symbols include the source
    types it must handle. Uses the module namespace directly.
    """
    imported_names = dir(_fixture_instantiator)
    assert "FrameworkSource" in imported_names, (
        "instantiator must import FrameworkSource for its dispatch arms"
    )
    assert "ModuleSource" in imported_names, (
        "instantiator must import ModuleSource (Task 7) to handle the new "
        "source variant in its dispatch"
    )
