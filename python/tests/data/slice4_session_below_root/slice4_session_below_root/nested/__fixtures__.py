"""A session-lifetime declaration below the rootdir package — illegal.

``session`` is the tier that does not constrain the scheduler, so anchoring it
narrower than the run attaches it to no boundary at all. ADR-0009 Rule 4.

``nested/`` is one directory below the testpath root, so the cap here is
``package``. Collection must fail before any test in this project runs.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="session")
def engine() -> str:
    return "unreachable — collection must fail before this runs"
