"""Opts this subtree out of the rootdir's autouse ``setup`` (#1716).

Same name, no ``autouse``. Inside this package it is the deepest visible
declaration, so it is what resolution returns — and because it is not autouse,
nothing queues it, and the ancestor's autouse fixture does not fire here.

It records nothing when it builds: the point of the test is that neither
fixture runs for these tests, so a log line here would be indistinguishable
from the ancestor firing.
"""

from __future__ import annotations

from collections.abc import Iterator

import oxitest as oxi


@oxi.fixture(lifetime="module")
def setup() -> Iterator[str]:
    """Deliberately not autouse — this declaration *is* the opt-out."""
    yield "nested-setup"
