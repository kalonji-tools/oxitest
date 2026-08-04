"""An aliased lifetime="process" declaration below the rootdir package.

ADR-0009 Rule 4 forbids this. Before #1859 the check read the prescan AST, which
cannot see `ox`, so the rule silently did not apply to this spelling.
"""

from __future__ import annotations

import oxitest as ox


@ox.fixture(lifetime="process")
def engine() -> str:
    return "unreachable — collection must fail before this runs"
