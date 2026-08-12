"""Format a source path for display inside a diagnostic (#1851).

A diagnostic names two files often enough that the *comparison* between them
is the message: ``fixture 'thing' in A shadows definition in B``. Printed
absolutely, A and B share a long identical prefix and differ in one segment
near the end, so the reader has to scan past the part that carries no
information to reach the part that does.

The base is the **project rootdir**, and not the working directory. A base
that moves with the reader makes the two halves different shapes: run from
inside ``api/v1`` and the pair renders as ``__fixtures__.py`` against
``../../admin/v1/__fixtures__.py``, which is the defect this module exists to
remove. The working directory is worse than merely mobile — ``os.chdir`` is
process-global, a test may call it, and :mod:`._cwd_guard` repairs only a
*deleted* directory and never a moved one.

A relative path is not resolvable on its own, so the reporter announces the
rootdir one time at the start of a run.

This is display only. ``FixtureDef.declaration_path`` stays canonical because
six consumers compare, sort, or test its exact value.
"""

from __future__ import annotations

__all__ = ["format_path"]

import os


def format_path(path: str, rootdir: str | None) -> str:
    """Return *path* as it should be shown to a reader.

    Four inputs are returned unchanged, and each one is a case where a
    relative form would be wrong rather than merely longer.

    Args:
        path: A canonical absolute path, or a sentinel such as
            ``<plugin:suite>`` or ``<builtin>``.
        rootdir: The project rootdir, or ``None`` when the session has no
            project.

    Returns:
        The path relative to *rootdir*, or *path* unchanged.

    Examples:
        The separator is spelled by the platform, so the example compares
        against :func:`os.path.join` rather than against a literal.

        >>> root = os.path.join(os.sep, "proj")
        >>> format_path(os.path.join(root, "pkg", "api.py"), root) == os.path.join(
        ...     "pkg", "api.py"
        ... )
        True
        >>> format_path("<plugin:suite>", root)
        '<plugin:suite>'
        >>> format_path("<builtin>", root)
        '<builtin>'
    """
    # A sentinel is not a path. `<plugin:suite>` and `<builtin>` name where a
    # fixture came from when there is no file to name, and `os.path.relpath`
    # would happily turn either into a climb out of the rootdir.
    if path.startswith("<"):
        return path

    # `create_session` accepts `rootdir=None` for oxitest's own bootstrap,
    # which builds a session before a rootdir is known. A bootstrap diagnostic
    # must still print something true.
    if rootdir is None:
        return path

    try:
        relative = os.path.relpath(path, rootdir)
    except ValueError:
        # Windows raises when the two paths are on different drives. A plugin
        # installed on another drive than the project reaches this, and it is
        # reachable only there: a POSIX filesystem has one root, so every pair
        # of paths has a common ancestor.
        return path

    # A climb out of the rootdir is longer than the absolute path it replaces —
    # a `site-packages` plugin measured 9 levels — and it is harder to read.
    # The absolute path was already the better answer for these.
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return path

    return relative
