"""Make the project rootdir importable from test modules (#1780).

Test modules load under synthetic names via ``spec_from_file_location``
(``_loader.py:50``) and oxitest otherwise never touches ``sys.path``, so a test
module cannot import a sibling utility module by any spelling. This module
closes that gap, which is what makes the helper-system retirement (#1700,
#1720) survivable.

**Append, never insert.** Appending can only make previously-unresolvable names
resolvable; it cannot change a resolution that already succeeds, so an
installed distribution always wins over a same-named directory in the tree.
Prepending would silently change which copy of the code under test is imported
— a behaviour change this module deliberately does not make. See spec decision
D2 on #1780.
"""

from __future__ import annotations

__all__ = ["ensure_rootdir_importable"]

import sys
from pathlib import Path


def ensure_rootdir_importable(rootdir: str) -> None:
    """Append *rootdir* to ``sys.path`` unless it is already present.

    Idempotent: the appended entry is this function's own canonical form, so
    a repeat call finds it by string identity. Worker processes call this
    once per task and the serial path once per session. ``/a/b`` and
    ``/a/b/`` name one directory because the argument is resolved before the
    comparison, and a symlinked *argument* is stored resolved.

    Entries other code placed on ``sys.path`` are not examined. A foreign
    spelling of the same directory — from ``PYTHONPATH`` or an installed
    ``.pth`` file — therefore yields one duplicate, which is harmless and
    does not accumulate.

    No entry is resolved, so ``''`` on ``sys.path`` is inert here — it
    cannot equal an absolute path — and needs no guard (#1786).

    A non-existent *rootdir* is appended anyway — Python ignores unusable
    ``sys.path`` entries, and refusing would mean inventing a diagnostic for a
    case ``find_rootdir`` makes near-unreachable.
    """
    resolved = str(Path(rootdir).resolve())
    if resolved not in sys.path:
        sys.path.append(resolved)
