"""Truthful dotted names for loaded test modules (#1680).

Test modules load through ``spec_from_file_location`` under a synthetic name,
so the module object gets ``__package__ == ''`` and a ``__name__`` that names
nothing. Relative imports have no anchor, and any library reading the caller
frame's ``__name__`` sees a name matching no module.

This module computes the dotted name a file *would* have if imported normally,
and returns it only when that name resolves back to the same file. ``None``
means "no truthful name exists here", and the caller keeps the synthetic name
it uses today — so a file the import system cannot reach behaves exactly as it
did before this change.

**The check performs no import.** ``importlib.util.find_spec`` answers
correctly but imports the parent package as a side effect: measured executing
an adopter's ``tests/__init__.py``, and under a shadowed layout it executed the
wrong project's. A correctness check must not run adopter code, so the
top-level segment is resolved against ``sys.path`` by hand.

``importlib.machinery.PathFinder`` was the other candidate and is not usable.
It imports nothing, but it searches an explicit directory list and so never
asks which ``sys.path`` entry owns the dotted name — measured adopting both a
shadowed name and a name whose rootdir is not importable at all.

Identity is compared with ``os.path.samestat``, never by comparing path
strings, for the reason ``already_imported`` gives: one file has more than one
spelling (#1957, #2018).
"""

from __future__ import annotations

import keyword
import os
import sys
from functools import lru_cache
from pathlib import Path

__all__ = ["dotted_name_for"]


@lru_cache(maxsize=4096)
def _owner_for(path_entries: tuple[str, ...], top: str) -> Path | None:
    """Return the entry of *path_entries* that wins for *top*, or ``None``.

    First match wins, which is import resolution order. ``''`` means the
    current directory, as it does for the import system.

    Memoized on the whole search path rather than on *top* alone: a session
    calls ``ensure_rootdir_importable`` (``_session_factory``), so ``sys.path``
    grows mid-process and a name-only memo would answer every later module
    from a pre-append ``sys.path``. "Nothing owns this name" is a real answer
    and is cached like any other — ``lru_cache`` records a ``None`` return as
    readily as a ``Path``.

    The scan costs about 176 us per module unmemoized, measured on Linux
    x86_64 over a ~20-entry ``sys.path``, which is 0.335 s across this
    repository's own test files.
    """
    for entry in path_entries:
        base = Path(entry) if entry else Path.cwd()
        if (base / top).is_dir() or (base / f"{top}.py").is_file():
            return base
    return None


def _candidate_parts(path: Path, rootdir: str) -> tuple[str, ...] | None:
    """Return the rootdir-relative name parts, or ``None`` if none is legal.

    This answers "could this file have a dotted name at all", which is a
    question about spelling. Whether that name is *truthful* is a separate
    question, answered against ``sys.path`` by the caller.
    """
    try:
        rel = path.relative_to(Path(rootdir))
    except ValueError:
        return None
    parts = rel.with_suffix("").parts
    if not parts:
        return None
    if parts[-1] == "__init__":
        # 'pkg.__init__' would build a second copy of the package 'pkg'.
        return None
    if any(not part.isidentifier() or keyword.iskeyword(part) for part in parts):
        return None
    return parts


def dotted_name_for(module_path: str, rootdir: str | None) -> str | None:
    """Return the importable dotted name for *module_path*, or ``None``.

    ``None`` on every uncertainty: no rootdir, a path outside it, a segment
    that cannot appear in a dotted name, an ``__init__.py``, no owner for the
    top-level name, or an owner whose file is not this file. Declining is
    always safe — it is what the loader did before this existed.
    """
    if rootdir is None:
        return None
    path = Path(module_path)
    parts = _candidate_parts(path, rootdir)
    if parts is None:
        return None
    owner = _owner_for(tuple(sys.path), parts[0])
    if owner is None:
        return None
    resolved = owner.joinpath(*parts).with_suffix(".py")
    try:
        # PTH116 prefers Path.stat(). os.stat is kept because the result is
        # consumed by os.path.samestat, which has no pathlib equivalent —
        # both halves of one comparison stay in one idiom, as in _loader.
        same = os.path.samestat(os.stat(resolved), os.stat(path))  # noqa: PTH116
    except OSError:
        return None
    return ".".join(parts) if same else None
