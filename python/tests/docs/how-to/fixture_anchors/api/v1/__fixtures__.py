"""Fixtures anchored one level below `api/`, for the B1 boundary example.

``package`` lifetime is safe to demonstrate here: this directory holds a single
test module, so the subtree collapse that the tier buys costs no parallelism
and stays silent.
"""

from __future__ import annotations

import oxitest as oxi


# fmt: off
# --8<-- [start:package-lifetime]
@oxi.fixture(lifetime="package")
def endpoint() -> str:
    return "https://example.test/v1"
# --8<-- [end:package-lifetime]
# fmt: on
