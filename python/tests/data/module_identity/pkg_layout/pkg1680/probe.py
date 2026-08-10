"""Stands in for a library that reads the caller's module name.

loguru's ``logger.disable(name)`` inspects the calling frame's ``__name__`` to
decide whether to silence that caller. A synthesized name matches nothing, so
the call silences nothing — symptom 2 of #1680.
"""

from __future__ import annotations

import sys


def caller_module_name() -> str:
    """Return the ``__name__`` of the module that called this function."""
    # loguru.logger.disable does exactly this, and it is symptom 2 of #1680.
    return sys._getframe(1).f_globals.get("__name__", "")  # noqa: SLF001
