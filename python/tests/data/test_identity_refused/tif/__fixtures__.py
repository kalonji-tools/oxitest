"""A function-lifetime fixture reached BENEATH a module-lifetime consumer.

Both fixtures are legal on their own. The consumer caches the inner value for
the whole module, so the inner fixture stops being per-test even though it
declared ``lifetime="function"`` (#1879).
"""

from __future__ import annotations

import oxitest as oxi
from oxitest import Fixture, TestIdentity


@oxi.fixture(lifetime="function")
def inner(test: TestIdentity) -> str:
    """Per-test by declaration, cached by its consumer in practice."""
    return f"inner_{test.name}"


@oxi.fixture(lifetime="module")
def outer(inner: Fixture[str]) -> str:
    """Built one time for the module, pinning whatever inner returned."""
    return f"outer[{inner}]"
