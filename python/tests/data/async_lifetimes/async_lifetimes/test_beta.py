"""Second module — proves module-lifetime instances do not cross module lines.

Whatever ``test_alpha`` built at ``lifetime="module"`` must have been disposed
before these tests run, and these tests must get their own instances.
"""

from __future__ import annotations

import os
from pathlib import Path

from oxitest import Fixtures


def _record(event: str) -> None:
    with Path(os.environ["ASYNC_LIFETIMES_LOG"]).open("a") as fh:
        fh.write(f"{event}\n")


async def test_beta_one(fx: Fixtures) -> None:
    per_module = await fx.async_lifetimes.per_module
    gen_module = await fx.async_lifetimes.per_module_gen
    assert per_module.startswith("per_module-"), (
        f"module-lifetime async fixture must arrive awaited, got {per_module!r}"
    )
    assert gen_module.startswith("per_module_gen-"), (
        f"module-lifetime async generator must yield its value, got {gen_module!r}"
    )
    _record("USE beta_one")


async def test_beta_two(fx: Fixtures) -> None:
    per_module = await fx.async_lifetimes.per_module
    assert per_module.startswith("per_module-"), (
        f"module-lifetime async fixture must arrive awaited, got {per_module!r}"
    )
    _record("USE beta_two")
