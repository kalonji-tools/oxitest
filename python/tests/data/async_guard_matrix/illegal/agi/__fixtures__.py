"""Publishes the registrar declared in ``_registrar.py``.

The registrar scans this module for ``@oxi.fixture`` markers, and the
decorator returns the function unchanged, so the re-export is the whole
registration and both sides keep the same function objects.

See ``_registrar.py`` for why the declarations do not live here.
"""

from __future__ import annotations

from agi._registrar import conn, other

__all__ = ["conn", "other"]
