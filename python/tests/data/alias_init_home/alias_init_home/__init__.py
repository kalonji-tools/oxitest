"""`__init__.py` is a legal declaration home, and the fully-silent failure row.

`reserved=false` suppressed even the mistyped-alias hint here, so before #1859 an
unrecognized spelling in this file produced no error at all — just a
fixture-not-found at test time, pointing nowhere near the cause.
"""

from __future__ import annotations

import oxitest as ox


@ox.fixture(lifetime="package")
def shared_db() -> str:
    return "db"
