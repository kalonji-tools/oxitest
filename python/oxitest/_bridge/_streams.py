"""Declare UTF-8 on this process's std streams (#2004).

Both entry points call this before reading or writing anything. Without it the
streams fall back to the locale codec, which on Windows is cp1252 while both
ends of the worker task wire are UTF-8: the coordinator writes raw UTF-8 with
``serde_json::to_writer`` and the worker decodes it as cp1252, so a single
non-ASCII character in a path makes every result come back under a node id the
coordinator never issued — and the run reports ``no tests ran`` with exit 0.

Unconditional rather than guarded on ``sys.platform``. On POSIX the streams are
already UTF-8, so this is a semantic no-op; a platform arm would be exercisable
only on the Windows job, and ``PYTHONIOENCODING=cp1252`` would stop being a
regression test that runs everywhere.
"""

from __future__ import annotations

import io
import sys

__all__ = ["force_utf8_streams"]


def force_utf8_streams() -> None:
    """Declare stdin/stdout/stderr as UTF-8, keeping each error handler.

    ``errors=stream.errors`` is load-bearing: a bare
    ``reconfigure(encoding="utf-8")`` resets the handler to ``strict``, and
    Windows ``sys.stderr`` defaults to ``backslashreplace``. Losing that turns
    a mangled-but-printed traceback into a raise inside the error path.

    A stream that is not a :class:`io.TextIOWrapper` is skipped rather than
    crashed on — ``StdCapture`` installs a ``StringIO``, and under ``pythonw``
    the streams can be ``None``.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors=stream.errors)
