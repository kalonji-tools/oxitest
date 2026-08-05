"""A process declaration below the implied rootdir package — illegal.

The project declares no `testpaths`, so the root is folded from its layout and
lands on `tests/`. `nested/` is one directory below it, so the cap here is
`package` and collection must fail before any test runs.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="process")
def engine() -> str:
    return "unreachable — collection must fail before this runs"
