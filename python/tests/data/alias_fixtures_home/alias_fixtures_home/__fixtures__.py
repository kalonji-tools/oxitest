"""A declaration home reached through an import alias prescan cannot name.

`ox` is not one of the three spellings `is_fixture_call` recognizes, but
registration is marker-attribute based, so this declares a real fixture. Before
#1859 prescan gated the import away and the fixture silently never existed.
"""

from __future__ import annotations

import oxitest as ox


@ox.fixture(lifetime="function")
def conn() -> str:
    return "connected"
