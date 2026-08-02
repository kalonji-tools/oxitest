"""The namespaced registrar, in a plain module rather than in ``conftest.py``.

Both this module and ``conftest.py`` must hand out the *same* function objects.
``executor.py``'s ``FixtureRef`` branch reaches ``get_fixture_in_namespace``
only when ``get_namespace_for_func`` returns a namespace, and that lookup
matches on function identity (``defn.source.func is raw``). ``conftest.py`` is
loaded by ``conftest_loader`` through ``spec_from_file_location`` under a
private module name, so a test module that imported ``agi.conftest`` directly
would execute the file a second time and hold different function objects — the
identity check would miss and resolution would fall back to the un-namespaced
route, which is not the route under test.

Importing from an ordinary module instead means both sides go through the one
``sys.modules["agi._registrar"]`` entry.

- **``name="db"``** gives the registrar a namespace; without one,
  ``get_namespace_for_func`` returns ``None`` even on an identity match.
- **``shared=True``** puts both fixtures at a lifetime wider than ``function``.
  That is the case ``AsyncDepGuardMiddleware`` cannot see: it inspects resolved
  kwargs for a coroutine, and a shared async fixture's kwarg is not one.

Two fixtures rather than one because ``strict = "abort"`` rejects a
single-case ``@oxi.parametrize``, and both cases must be illegal for the run's
exit code to mean what the acceptance test reads it as.
"""

from __future__ import annotations

import oxitest as oxi

from agi._kinds import Conn

db = oxi.Fixtures(name="db")


@db.fixture(shared=True)
async def conn() -> Conn:
    return Conn("conn")


@db.fixture(shared=True)
async def other() -> Conn:
    return Conn("other")
