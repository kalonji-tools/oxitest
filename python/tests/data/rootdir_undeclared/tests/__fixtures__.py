"""A process declaration in the implied rootdir package.

`tests/` is not the project root — `pyproject.toml` sits one level up — so a
rootdir package derived from the project root rejects this, and one derived
from the collected files under a narrowed run moves below it. Only a root
implied by an unnarrowed walk makes this legal under every invocation.
"""

from __future__ import annotations

import oxitest as oxi


@oxi.fixture(lifetime="process")
def engine() -> str:
    return "engine"
