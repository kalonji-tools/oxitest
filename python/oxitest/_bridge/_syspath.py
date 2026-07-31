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
    """Append *rootdir* to ``sys.path`` unless an equivalent entry is present.

    Idempotent: worker processes call this once per task and the serial path
    once per session. Comparison is by resolved path so that ``/a/b``,
    ``/a/b/`` and a symlinked spelling of one directory do not accumulate
    duplicates.

    Empty entries are skipped during comparison rather than treated as
    equivalent to the current directory. ``''`` on ``sys.path`` is re-resolved
    against the *live* working directory on every import, so a test that
    changes directory would silently lose the rootdir; the absolute entry
    appended here does not move.

    A non-existent *rootdir* is appended anyway — Python ignores unusable
    ``sys.path`` entries, and refusing would mean inventing a diagnostic for a
    case ``find_rootdir`` makes near-unreachable.
    """
    resolved = Path(rootdir).resolve()
    for entry in sys.path:
        if entry and Path(entry).resolve() == resolved:
            return
    sys.path.append(str(resolved))
