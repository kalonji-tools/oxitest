"""Whether a namespace can be written as ``fx.<namespace>.<name>``.

A namespace is *derived*, not declared: it is the basename of a fixture's
anchor directory, or a plugin's module name (ADR-0009 Rule 5). Nothing
constrains a directory name or a module path to the shape an attribute access
needs, so the derived value can be a string no user can write.

The predicate here is the whole rule, and it is exactly two clauses. Both were
established by parsing ``fx.<name>.x`` and asserting the result is a nested
``ast.Attribute`` rather than by reasoning about what "valid" ought to mean:

- ``str.isidentifier`` rejects ``integration-tests`` and ``2fast``. The first
  is the dangerous one — it *parses*, as ``fx.integration - tests.conn``, so
  the access never reaches oxitest and the run reports a missing fixture named
  ``integration``.
- ``keyword.iskeyword`` rejects ``class`` and ``def``, which are a
  ``SyntaxError`` at import, so the test module never collects.

**Builtins and soft keywords are reachable and stay legal.** ``fx.int.x``,
``fx.list.x``, ``fx.match.x``, ``fx.case.x``, ``fx.type.x`` and ``fx._.x`` all
parse as attribute access. A previous version of this module refused builtins
and soft keywords while accepting ``integration-tests``, so it was wrong in
both directions at once and ran on the plugin path only (#1782).
"""

from __future__ import annotations

__all__ = ["namespace_defect"]

import keyword


def namespace_defect(name: str) -> str | None:
    """Why *name* cannot be written as ``fx.<name>``, or ``None`` if it can.

    Returns the reason rather than a bool because the two callers report it
    differently: a namespace someone typed is an error, and a namespace
    derived from a directory or a module path is a warning. Each builds its
    own remedy, so this function formats no message and raises nothing.
    """
    if not name.isidentifier():
        return "not a Python identifier"
    if keyword.iskeyword(name):
        return "a Python keyword"
    return None
