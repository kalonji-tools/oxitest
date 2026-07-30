"""Path arithmetic for the ADR-0009 B1 boundary (#1713).

Deliberately not ``_boundary.py``: that module means *trust* boundaries
(``safe_call``, ``safe_type_hints``) and has six importers. B1 is a question
about the filesystem tree, and conflating the two words would make every future
reader guess which boundary a call site meant.

Every comparison here is component-wise. String prefixes are the one thing this
module exists to prevent — ``"/t/apiv2".startswith("/t/api")`` is True and the
two are siblings.

Paths arriving here are canonical-absolute: ``collector.rs`` canonicalizes every
collected path, so anchors and module paths are already in the same form and no
normalisation happens at this layer.
"""

from __future__ import annotations

__all__ = ["anchor_depth", "anchors_overlap", "is_visible"]

from pathlib import Path


def is_visible(*, anchor: str, defining: str, module_path: str) -> bool:
    """Whether code at *module_path* may resolve a fixture anchored at *anchor*.

    Two anchor kinds, per ADR-0009 Rules 1 and 3:

    - **inline declaration** — the anchor *is* the defining module, a file.
      Visible only from that exact module (Rule 1's module cap).
    - **package declaration** — the anchor is a directory. Visible from that
      directory and every descendant (Rule 3's B1 chain).

    ``anchor == defining`` is how the two are told apart without touching the
    filesystem: a package anchor is a directory and its defining module is the
    ``__fixtures__.py`` inside it, so the two can never be equal.

    *module_path* is the test's module at the top of a resolution chain and the
    resolving fixture's own anchor once the chain descends into dependencies —
    see ``_ResolutionContext.boundary_path``.
    """
    if anchor == defining:
        return module_path == anchor
    return Path(module_path).is_relative_to(anchor)


def anchor_depth(anchor: str) -> int:
    """Component count of *anchor* — the order behind "deepest visible wins"."""
    return len(Path(anchor).parts)


def anchors_overlap(first: str, second: str) -> bool:
    """Whether one anchor's subtree contains the other's.

    The registration-collision predicate. Two declarations sharing a
    ``(namespace, name)`` pair are a real clash only when some test can see
    both, and disjoint subtrees never can — namespaces are directory basenames,
    so ``tests/api/v1`` and ``tests/admin/v1`` both derive ``v1`` while being
    mutually invisible.
    """
    return Path(first).is_relative_to(second) or Path(second).is_relative_to(first)
