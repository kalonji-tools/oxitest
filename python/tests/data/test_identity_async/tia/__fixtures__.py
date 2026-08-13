"""The async function-lifetime route, isolated (#1879).

Isolated from ``test_identity_routes`` because of #2094: three fixtures sharing
the annotated type make a name-matched *async* fixture unresolvable, and that
defect is unrelated to test identity. One fixture here, so the collision that
#2094 describes cannot arise and this project measures only the async route.
"""

from __future__ import annotations

import oxitest as oxi
from oxitest import TestIdentity, Yields


@oxi.fixture(lifetime="function")
async def async_named(test: TestIdentity) -> Yields[str]:
    """Reaches _resolve_deps through _resolve_async_deps, not _instantiate."""
    yield test.name
