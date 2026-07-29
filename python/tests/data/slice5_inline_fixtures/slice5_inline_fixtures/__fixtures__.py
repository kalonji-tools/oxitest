"""A package-level fixture, visible to every file in this directory.

Present so the slice-5 tests can tell a correct module filter from a blanket
block on ModuleSource: an inline fixture must be invisible to sibling files, and
this one must stay visible to them. A filter that blocked both would satisfy the
isolation assertion just as well as a correct one.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="module")
def shared_label() -> str:
    return "package-level"
