"""Declares an inline fixture that a sibling module must not be able to reach.

Split out of ``slice5_inline_fixtures`` when the collection-time B1 gate
shipped (#1758). The violation it feeds used to be asserted *inside* the
sibling test with ``raises(Exception)``, which a collection-time refusal makes
unrunnable — the run is refused before any test body executes. The negative
case therefore moved to its own project, asserted by a wrapper that runs the
project as a subprocess, which is the pattern every other negative project in
this corpus already uses.

Keeping it in the original project would have taken the isolation pair down
with it: that project's whole point is that a *legal* package-level access
still works, and a refused collection proves nothing about it.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="module")
def per_module() -> str:
    return "inline"


def test_the_declaring_module_reaches_its_own_inline_fixture(
    per_module: oxi.Fixture[str],
) -> None:
    assert per_module == "inline", (
        "the declaring module must resolve its own inline fixture, or the "
        "sibling's refusal below could mean the fixture never registered"
    )
