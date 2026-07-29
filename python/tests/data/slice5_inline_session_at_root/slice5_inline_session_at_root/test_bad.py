"""An inline fixture declaring lifetime="session" AT the rootdir package.

This is the case only the home-kind cap catches. The location rule from #1711
permits `session` when the anchor IS the rootdir package, and this file sits
exactly there — so if the two axes were merged into one check, this would be
accepted and an inline fixture would outlive its module.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="session")
def cluster() -> str:
    return "unreachable — collection must fail before this runs"


def test_uses_it() -> None:
    assert True, "collection must fail before this test executes"
