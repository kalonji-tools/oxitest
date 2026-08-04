"""A declaration home that declares nothing, and mentions oxitest nowhere.

The pre-#1859 guard fired on decorator *shape* alone — any top-level function
with any decorator — so this file was a hard collection error accusing the user
of a mistyped oxitest import alias it never had.
"""

from __future__ import annotations

import functools


@functools.cache
def _lookup_table() -> dict[str, int]:
    return {"a": 1}
