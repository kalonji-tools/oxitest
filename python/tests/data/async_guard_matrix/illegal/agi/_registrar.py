"""The fixtures a sync test must not receive, in a plain importable module.

``test_illegal.py`` imports these function objects so a ``FixtureRef`` can name
them. That import also registers them a second time, as **inline** declarations
anchored at the test module — which is why the lifetime here is ``function``
rather than anything wider: ADR-0009 Rule 1 caps an inline declaration at
``module``, and a ``package`` declaration reached this way is refused at
registration.

``function`` is also the tier this project exists to pin. After ADR-0006
Amendment 2 it is the **only** lifetime at which a ``FixtureRef`` to an async
fixture is still illegal for a sync test. At every wider tier the value was
awaited on the shared session loop before the test started, so the sync test
receives it — see ``../legal/`` (#1876).

The refusal comes from ``AsyncDepGuardMiddleware``, which inspects the resolved
kwargs for a coroutine. A ``function``-lifetime async fixture is one; a wider
one is not.

Two fixtures rather than one because ``strict = "abort"`` rejects a
single-case ``@oxi.parametrize``, and both cases must be illegal for the run's
exit code to mean what the acceptance test reads it as.
"""

from __future__ import annotations

import oxitest as oxi

from agi._kinds import Conn


@oxi.fixture(lifetime="function")
async def conn() -> Conn:
    return Conn("conn")


@oxi.fixture(lifetime="function")
async def other() -> Conn:
    return Conn("other")
