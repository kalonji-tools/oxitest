"""The async fixture the ``FixtureRef`` cell names.

Declared here rather than in ``__fixtures__.py`` on purpose. A ``FixtureRef``
needs the fixture *function object*, so ``test_matrix.py`` imports it, and that
import registers it as an inline declaration anchored at the test module. A
second declaration in ``__fixtures__.py`` would then be shadowed, and the run
would emit a registration notice about a collision this project created for
itself.

Inline is legal at ``module`` lifetime — ADR-0009 Rule 1 caps an inline
declaration there — which is the tier this cell needs.
"""

from __future__ import annotations

import oxitest as oxi

from agm._kinds import Ref


@oxi.fixture(lifetime="module")
async def by_ref() -> Ref:
    return Ref()
