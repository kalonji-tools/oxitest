"""Binding types for this project's fixtures.

``Fixture[T]`` resolves by *type* before it resolves by name, so the binding
types have to be classes this project owns — a builtin like ``str`` would put
these fixtures in the same ``_by_type`` bucket as anything else in the run that
happens to return one, and the probe below would stop measuring what it claims
to.

They live in a plain module rather than in ``api/__fixtures__.py`` because a
declaration file is loaded under a synthesised module name
(``importer.register_module_source_fixtures_for_module``). Importing it again
by its package path from a sibling test would execute it a second time and
hand back a *different* class object, so the type-based lookup would miss and
the test would silently fall through to the name-based route — testing the
wrong half of ``resolve_param``. A normally-imported module keeps one class
object, which is what makes the type route the one under test.
"""

from __future__ import annotations


class ApiConnection:
    """Return type of the fixture the boundary tests reach across."""

    def __init__(self, label: str) -> None:
        self.label = label


class LedgerHandle:
    """Return type shared by two fixtures in mutually invisible packages.

    ``FixtureRegistry.resolve`` reads ``_by_type``, which — unlike
    ``get_visible`` — applies no B1 filtering. Two fixtures sharing this type,
    one reachable from the test and one not, is what makes that visible.
    """

    def __init__(self, label: str) -> None:
        self.label = label
