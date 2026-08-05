"""Two async package fixtures for pkg_b, one per registration route (#1839).

The async half had its own root cause: the boundary a teardown is filed under
is chosen when it is *registered*, and ``package`` was not one of the cases,
so ``drain_boundary`` had nothing to find whatever key ``end_package`` used.

There are two registration sites and they disagreed with each other, so both
are declared here rather than one per package. Putting one route in each
package would leave whichever package runs *last* unconstrained — its boundary
and the end of the run are the same instant, which is the blind spot this
whole issue is about. Declaring both in both packages means the first package
to run constrains both routes, whichever one that turns out to be.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import oxitest as oxi


def record(event: str) -> None:
    """Append one event line to the log named by ``ASYNCLOG``."""
    with Path(os.environ["ASYNCLOG"]).open("a") as handle:
        handle.write(f"{event}\n")


@oxi.fixture(lifetime="package")
async def lazy_b() -> AsyncIterator[str]:
    """Reached by ``await fx.pkg_b.lazy_b`` — the register_teardown site."""
    record("SETUP b-lazy")
    yield "b-lazy"
    record("TEARDOWN b-lazy")


@oxi.fixture(lifetime="package")
async def eager_b() -> AsyncIterator[str]:
    """Reached by ``Fixture[T]`` injection — the resolve site."""
    record("SETUP b-eager")
    yield "b-eager"
    record("TEARDOWN b-eager")
