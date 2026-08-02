"""The example ``builtins.md`` used to teach, kept verbatim in shape.

``ctx.name`` inside a fixture body used to return ``"db_schema"`` — the
fixture's own name — so every test in the run received the same
"test-specific" schema. It now raises ``TestIdentityUnavailableError``.
"""

from __future__ import annotations

import oxitest as oxi
from oxitest import TestContext


@oxi.fixture(lifetime="function")
def db_schema(ctx: TestContext) -> str:
    return f"test_{ctx.name}"
