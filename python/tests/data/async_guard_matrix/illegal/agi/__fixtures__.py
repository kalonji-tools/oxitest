"""Every cell that stays illegal after ADR-0006 Amendment 2.

Two groups, and only one of them is declared here.

- ``conn`` / ``other`` live in ``_registrar.py`` and are **not** re-exported.
  ``test_illegal.py`` imports them, which registers them as inline
  declarations anchored at the test module, so a second declaration here would
  be shadowed and the run would report a collision this project created for
  itself. That module's docstring says why they sit at ``function`` lifetime.
- ``wide_module`` and ``wide_package`` are declared here, because nothing
  imports them — the proxy reaches a fixture by name. The proxy
  refuses a sync test at every lifetime, including the two where the
  ``Fixture[T]`` route succeeds, and that asymmetry is the whole content of
  Amendment 2. Without a project asserting it, a future change could relax the
  proxy and every gate would stay green.
"""

from __future__ import annotations

import oxitest as oxi

from agi._kinds import Conn

__all__ = ["wide_module", "wide_package"]


@oxi.fixture(lifetime="module")
async def wide_module() -> Conn:
    return Conn("wide-module")


@oxi.fixture(lifetime="package")
async def wide_package() -> Conn:
    return Conn("wide-package")
