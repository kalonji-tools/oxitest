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

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=4096)
def _parts(path: str) -> tuple[str, ...]:
    """*path* split into components, memoised.

    ``Path`` construction is the whole cost of this module — roughly 31 µs a
    call — and it was invisible while every predicate here ran once per
    declaration. It is not any more: ``FixtureRegistry.register`` scans every
    prior def sharing a name (#1766 Decision 2), so registering *k*
    declarations of one name decomposes anchors O(k²) times, and
    :func:`is_visible` runs on every fixture resolution.

    Memoising the decomposition rather than reimplementing it keeps
    ``pathlib`` as the authority on what a component is. Keys are canonical
    absolute paths — ``collector.rs`` canonicalises and
    ``_module_source_registrar`` reconciles ``__file__`` — so they are stable
    and bounded by the number of distinct anchors in a run.
    """
    return Path(path).parts


def _is_prefix(prefix: tuple[str, ...], parts: tuple[str, ...]) -> bool:
    """Whether *prefix* is a leading component run of *parts*.

    ``Path(a).is_relative_to(b)`` is exactly this predicate over their
    components, equal paths included. Spelled out rather than delegated so the
    comparison stays component-wise: string prefixes are the one thing this
    module exists to prevent.

    The empty prefix is rejected rather than treated as universal. ``()`` is
    what ``Path("").parts`` yields, and a bare slice test would make it a
    prefix of everything — the exact inversion ``_canonical_module_path``
    guards against when it keeps an empty ``__file__`` empty so a path-less
    module stays obviously path-less instead of matching the project root.
    ``Path(x).is_relative_to("")`` is ``False``, and so is this.
    """
    return bool(prefix) and parts[: len(prefix)] == prefix


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

    That test is string equality across a layer seam, so it is only as good as
    the canonical form promised in the module docstring. *anchor* comes from
    ``collector.rs``; *defining* comes from a Python module's ``__file__``,
    which the import machinery does not resolve. ``_module_source_registrar``
    canonicalises the latter at registration so the two arrive comparable —
    move that normalisation and the discriminator misreads an inline
    declaration as a package one the moment a symlink separates the spellings.

    *module_path* is the test's module at the top of a resolution chain and the
    resolving fixture's own anchor once the chain descends into dependencies —
    see ``_ResolutionContext.boundary_path``.
    """
    if anchor == defining:
        return module_path == anchor
    return _is_prefix(_parts(anchor), _parts(module_path))


def anchor_depth(anchor: str) -> int:
    """Component count of *anchor* — the order behind "deepest visible wins"."""
    return len(_parts(anchor))


def anchors_overlap(first: str, second: str) -> bool:
    """Whether one anchor's subtree contains the other's.

    The registration-collision predicate. Two declarations sharing a
    ``(namespace, name)`` pair are a real clash only when some test can see
    both, and disjoint subtrees never can — namespaces are directory basenames,
    so ``tests/api/v1`` and ``tests/admin/v1`` both derive ``v1`` while being
    mutually invisible.
    """
    first_parts, second_parts = _parts(first), _parts(second)
    return _is_prefix(second_parts, first_parts) or _is_prefix(
        first_parts, second_parts
    )
