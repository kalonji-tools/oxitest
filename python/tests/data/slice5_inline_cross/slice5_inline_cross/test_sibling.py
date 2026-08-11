"""Reaches for another file's inline fixture. Must be refused.

An inline declaration is capped at its own module (ADR-0009 Rule 1), so this
access is illegal however the run is scheduled. The refusal is asserted by the
wrapper in ``test_fixtures_redesign_slice5.py``, not here: a collection-time
refusal means this body never runs, so an in-test ``raises`` could not observe
it.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_another_files_inline_fixture_is_not_visible(fx: Fixtures) -> None:
    _ = fx.test_inline.per_module
