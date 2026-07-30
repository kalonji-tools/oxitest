"""Reaches for a sibling module's inline fixture.

Expected to error. The point is not *that* it errors but that it errors the
**same way** serially and under ``-n``: inline declarations register only in
the worker that imported their module, so any diagnostic that consults the
unfiltered catalog varies with worker assignment.
"""

from __future__ import annotations

from oxitest import Fixtures


def test_a_sibling_module_cannot_reach_it(fx: Fixtures) -> None:
    # Act — expected to raise FixtureNotFoundError
    value = fx.inline_only

    # Assert — unreachable; inline fixtures are capped at their own module
    assert value is None, (
        "reaching this line means an inline fixture escaped its declaring "
        "module, which would make its lifetime cap meaningless"
    )
